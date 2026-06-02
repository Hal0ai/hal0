# 00 — Brain Redesign: Index & Executive Summary

**Status:** PLANNING — pre-grilling, not yet decided, not yet implemented.
**Date:** 2026-06-02.
**Authors:** a multi-agent research wave (one dossier per agent), synthesized in doc 06.

> This is the **front door** to the brain-redesign dossiers. Read it after a
> reboot to reload the whole context, then dive into the numbered docs as needed.

---

## TL;DR — what this redesign is

hal0's "brain" is being redesigned as **two durable tiers behind one unchanged
access plane**:

- **The Engine (Tier E)** — a machine-owned, fast, structured semantic/episodic
  store. The recommendation is to **replace Cognee with Hindsight** (vectorize.io's
  biomimetic agent-memory engine: `retain`/`recall`/`reflect` + automatic
  consolidation of raw facts into evidence-grounded, freshness-tracked
  *observations*). It captures every turn/fact cheaply, recalls with multi-strategy
  token-budgeted retrieval, and fixes the staleness gap Cognee-as-used has today.
- **The Wiki (Tier C)** — a human-legible, curated, cross-linked **compiled-knowledge
  layer**: the `hal0-wiki` fork of `obsidian-wiki` (Karpathy's "LLM Wiki" pattern over
  an Obsidian-flavored markdown vault). It holds the knowledge a person reads, edits,
  audits, and trusts. "Compile, don't retrieve."

The Engine is the hippocampus; the Wiki is the published notebook. They relate
through a **strictly one-way, gated promotion pipeline** (Engine observation →
gated promotion → Wiki page → re-embedded back into the Engine as a top-of-hierarchy
index entry), so there is exactly **one canonical owner per fact-class** and the two
stores cannot silently disagree. Every consumer — Hermes, external Claude Code,
pi-coder, OpenWebUI, the dashboard — reaches both tiers through the *unchanged*
`/mcp/memory` + `/api/memory/*` contract with the `X-hal0-Agent` namespace rule, so
the brain is felt as **deeper**, not as a new service bolted on. The whole thing runs
**fully self-hosted on Strix Halo** (CT 105) with no cloud dependency.

---

## Why now — the motivating findings

The audit (doc 03) and landscape brief (doc 05) make the case:

1. **Cognee's graph is dead weight.** hal0 runs a 3-database knowledge-graph engine
   (SQLite + LanceDB + Kuzu) but graph extraction is **default-OFF**, `cognify` runs
   only as a fire-and-forget background task whose output is **never read**, and
   `search` is always pure-vector (`SearchType.CHUNKS`). We pay Cognee's complexity
   and get RAG.
2. **The graph is off because it can't run locally.** `.cognify()` needs an LLM to
   reliably emit structured JSON; sub-32B local models on Strix Halo can't (noisy
   graphs, 10-min tenacity hangs — independently confirmed in the field). The graph
   hal0 disabled is the graph hal0 *cannot reliably run locally*.
3. **pgvector + sidecar reality.** A hand-rolled **SQLite sidecar** is the real
   source of truth for dataset/tags/source/metadata/timestamp and for *all*
   filtering, joined to Cognee chunks by **fragile text equality**. In practice hal0
   is doing the job of `pgvector + a WHERE clause` with three databases behind it.
4. **Staleness handling gap.** Append-only vector memory accumulates contradictions
   ("user prefers React" + "user switched to Vue") and surfaces both. hal0 today runs
   **neither** consolidation (Hindsight observations / Cognee Memify) **nor** an
   active lint pass. This is where naive vector memory dies.
5. **Bolt-on risk.** The redesign's explicit non-goal is a second front door. It must
   attach at seams that already exist (the engine seam, Hermes `prefetch`/`sync_turn`,
   the embed/rerank slots, `/mcp/memory`, the dashboard Memory tab).
6. **Issue #317 is NOT a driver.** The known namespace-forcing bug
   (`/api/memory/add` forcing `dataset="shared"`) is **already FIXED** (PRs #366/#369,
   shared `hal0.memory.namespace` module). Treat it as closed; the auto-memory entry
   `hal0_memory_dataset_namespace_bug` is stale and should be marked resolved.

---

## Reading guide

| # | Title | Read this for… | ~Length |
|---|---|---|---|
| **00** | Index & Executive Summary | The front door — TL;DR, why-now, the recommendation in one page, open questions, glossary. (this file) | short |
| **01** | Hindsight Feature Inventory | Exhaustive, accurate feature map of the candidate engine: retain/recall/reflect, observations, memory banks, **bank templates**, documents, ops/admin CLI, webhooks, TEMPR retrieval, fully-local deployment, MCP/Hermes/Claude-Code integrations, hal0 relevance, and a Gaps list (Strix-Halo accel undocumented, webhook registration, etc.). | ~760 lines |
| **02** | hal0-wiki Research | What the `obsidian-wiki`/`hal0-wiki` fork actually is (a pure-stdlib installer + 38 markdown skill files; **no daemon**), the Karpathy LLM-Wiki pattern, vault structure, the 38 skills, package internals, the AGENTS.md contract, what's "Hal0'd" so far (nothing — byte-identical fork), and hal0 integration relevance (QMD→Engine swap, who maintains the vault, where it lives). | ~510 lines |
| **03** | Current Memory Audit (as-built) | Ground truth on the existing Cognee subsystem: the one-file `CogneeWrapper` seam, MCP/REST/dispatcher/CLI/Hermes-plugin layers, the data model (Cognee vs sidecar), every known defect (#317 fixed, wrong-route Hermes client bug, fragile text-join, dead Kuzu graph, test gaps), and the swap-seam verdict. | ~540 lines |
| **04** | Consumer Surface | The complete map of every brain *consumer* (agents, apps, MCP surfaces, slots, dashboard views), with `file:line` citations and a produces/consumes/wiki matrix. The prioritized "where to hook in to feel native" integration map (Tiers A/B/C). | ~485 lines |
| **05** | Memory Landscape & Tradeoffs | The decision frame: taxonomy of memory paradigms, a scored comparison matrix for hal0's reality, the **two-tier brain thesis** (+ honest counter-cases), where Cognee fits/exits, multi-agent namespacing/trust, self-host constraints on Strix Halo, and 8 open grilling-seed questions. | ~455 lines |
| **06** | Proposed Architecture | **The synthesis & recommendation.** Thesis + principles, the layered model, the `MemoryProvider` ABC (the linchpin), the Hindsight engine decision + local wiring table, the Wiki layer design, the **promotion model** (critical), per-consumer deep-integration map, bank-templates as a hal0 primitive, keep/change/delete, and 11 risks/open questions. | ~690 lines |
| **07** | Implementation / phasing roadmap | The staged plan — Phase 0 `MemoryProvider` ABC + conformance suite (no behavior change), Phase 1 Hindsight shadow + **eval hard-gate**, Phase 2 cutover, Phase 3 wiki, Phase 4 promotion pipeline, Phase 5 deep consumer integration, Phase 6 polish. Dependency graph + per-phase rollback + open-question blockers. | ~570 lines |
| **08** | Grilling decision tree | The dependency-ordered tree of every open decision (S/A/B/C/D/E/F branches), each with options, my recommended answer, and the sharpest counter-challenge. The working doc for the post-reboot `/grill-me` session. | ~380 lines |

> All nine docs (00–08) are present in this directory as of 2026-06-02. Doc 08 (the
> grilling decision tree) is the working document for the post-reboot session; docs
> 01–05 are research, 06 is the recommendation, 07 is the build sequence.

---

## Executive summary of the recommendation (faithful to doc 06)

**The layered model.** Three layers:

- **(a) Engine — Hindsight (Tier E):** machine-owned, fast, structured. Owns raw
  turns/facts and the *observations* derived from them. Verbs: `retain` (LLM extract →
  4-edge graph → background consolidation), `recall` (TEMPR 4-way → RRF → cross-encoder
  → token budget), `reflect` (agentic loop with disposition). **Canonical for raw
  episodic recall.** Banks map 1:1 to hal0 namespaces.
- **(b) Wiki — hal0-wiki (Tier C):** human-legible, curated, cross-linked markdown with
  provenance/confidence/lifecycle/typed-relationship frontmatter. **Canonical for
  curated knowledge** (procedural playbooks, stable semantic facts, architecture notes,
  agent identity write-ups). No daemon — a directory of markdown plus skill files the
  agent executes.
- **(c) Access plane:** the one front door — `/mcp/memory` (4 memory tools + new
  `wiki_search`/`wiki_get`), `/api/memory/*` (CRUD + graph gate), and a sibling
  `/api/wiki/*`. The `X-hal0-Agent` → namespace rule (resolved server-side in
  `hal0.memory.namespace`) is preserved verbatim. **Adding the Wiki adds tools on the
  same server, not a new service.**

**Engine decision — Hindsight, wired locally.** Replace Cognee with Hindsight, staged
behind the new ABC, **eval-gated** (run hal0's δ/eval harness on Cognee-as-used vs
Hindsight vs plain-pgvector first; flip the default only after). Local wiring on CT 105:
`hindsight-api` as a systemd unit; **embedded `pg0`** (replaces all three Cognee stores);
**embeddings → the embed capability slot** (TEI/OpenAI-compatible, not a bundled
embedder); **rerank → the :8086 rerank slot** already wired; **extraction LLM →
lemond:13305** (Lemonade gateway, not Hindsight's bundled llamacpp). Assume CPU for
embed/rerank (Strix-Halo/ROCm accel is undocumented — validate). Degrade ladder: full
retain/recall/reflect → `LLM_PROVIDER=none` recall-only (still better than
Cognee-as-used) → pgvector/no-op fallback with a "no engine" dashboard state.

**Wiki layer.** Vault at `/var/lib/hal0/wiki` (git-backed), **Hermes-as-librarian**
(skills installed at provision time, a systemd `daily-update` timer for
freshness/index/lint). Viewed primarily via a **dashboard render** (markdown +
`wiki-export` `graph.html`). The single biggest "Hal0'd" change to the fork: **swap QMD
for the Engine** as the search index (on every vault write, call `register_compiled`;
on query, semantic pass against the Engine first, Grep fallback). Keep `memory-bridge`
("what does Hermes know that Claude-Code doesn't") wired to `X-hal0-Agent` identity.

**The recommended promotion model.** Gated, one-way: Engine observation
(`proof_count ≥ N` *or* `freshness == 'stable'`) → **promotion gate** → Wiki page
(`provenance: inferred`, `lifecycle: draft`) → `register_compiled()` → Engine
`kind='wiki'` mental_model (top of the recall hierarchy: wiki → observations → facts).
Consolidation writes the Engine's own observations **only** — never auto-writes the
Wiki. Gate by namespace: `private:<agent>` **auto-promotes**; `shared`/`project:<id>`
are **gated** (librarian agent drafts, human approves via the dashboard inbox, reusing
the destructive-call approval-queue UX). Wiki → Engine is one-way and derived; the Wiki
page is canonical, the Engine copy is an index. Main risk: the gate becomes a bottleneck
or a rubber stamp.

**The `MemoryProvider` seam.** The prerequisite to everything. Today Cognee is contained
to one file and one construction site but there is **no formal ABC**. The plan promotes
the implicit five-method contract into an explicit `MemoryProvider` ABC/Protocol
(`src/hal0/memory/provider.py`) with engine-neutral value types, the core five
(`add`/`search`/`list_items`/`delete` + runtime-flip helpers `graph_status`/
`set_graph_enabled`/`set_rerank_enabled`), and **optional** Hindsight-era methods
(`recall`, `reflect`, `consolidate`, and the promotion seam `register_compiled`) with
safe defaults so a pgvector fallback still satisfies the contract. `MemoryItem.id`
becomes the join key (retiring the fragile text-equality sidecar). A **parametrized
conformance suite** runs against every provider and de-risks the swap. Only the single
construction site at `api/__init__.py:1108` changes
(`CogneeWrapper()` → `provider_from_config(cfg)`).

---

## The big open questions

The full decision tree lives in **doc 08** (grilling, authored separately). The headline
unresolved forks (from doc 05 §7 and doc 06 §10):

1. **Promotion gate** — bottleneck or rubber stamp? Is `private:<agent>`-auto +
   `shared`-gated the right split on a single-user box where proof-count corroboration
   may never accrue?
2. **Eval not yet run** — "Hindsight recalls better than Cognee-as-used" is an
   architectural judgement, not a benchmark. If the delta is null, do we still pay the
   migration cost for the consolidation/observations *upgrade path* alone?
3. **Librarian centralisation** — Hermes-as-librarian is a single failure/trust point;
   split into a dedicated curator persona, or accept the coupling?
4. **Strix-Halo accel** — CPU embed/rerank is an *assumption*; bge-on-iGPU may change
   the embedding model (forcing a re-embed). Unvalidated.
5. **Local structured-output: invest or route?** Do we ever stand up a
   grammar-constrained / tool-call 32B+ extraction path, or permanently design for
   recall-only + hand-curated Wiki?
6. **Working-memory budget ownership** — recall token budget vs the orchestrator: who
   arbitrates context space across wiki pages / observations / raw facts?
7. **External-agent default read** — should an external Claude Code session read the
   `shared` brain by default? (proposed: read-yes / shared-write-no.)
8. **Hindsight bundled-daemon port collision** (`:9077`), **webhook per-bank
   registration** (may be server-wide only), **migration data fidelity** (some
   text-joined Cognee rows may not cleanly map), and the **YAGNI counter-case** (is the
   two-tier design over-built for v1 on a single-user box under the ~100k-token wiki
   ceiling?).

---

## Status & provenance

- These are **PLANNING documents**, produced **2026-06-02** by a multi-agent research
  wave (one dossier per agent), **pre-grilling**. Nothing here is decided or implemented.
- The recommendation in doc 06 *supersedes (proposed)* ADR-0005 (Cognee) and ADR-0014
  (graph gate) and *extends* ADR-0011 (`agents` dataset) and ADR-0012 (`X-hal0-Agent`,
  no-auth) — but only once decided and shipped.
- **Source dossiers:** `01-hindsight-research.md`, `02-hal0-wiki-research.md`,
  `03-current-memory-audit.md`, `04-consumer-surface.md`, `05-memory-landscape.md`,
  `06-proposed-architecture.md`, `07-integration-roadmap.md`, and
  `08-grilling-decision-tree.md` — all present in this directory.
- The **`hindsight-docs` skill is installed locally** at `~/.agents/skills/hindsight-docs/`
  (SKILL.md + references for architecture, APIs, config, best-practices) and was the
  primary source for doc 01.

---

## Glossary

- **Engine (Tier E)** — the machine-owned, fast, structured memory store (the candidate
  is Hindsight). Owns raw episodic turns/facts and the consolidated observations derived
  from them. Optimised for recall, not human reading.
- **Wiki layer (Tier C)** — the human-legible, curated, cross-linked markdown corpus
  (`hal0-wiki` over an Obsidian vault). Canonical for curated knowledge. "Compile, don't
  retrieve."
- **retain / recall / reflect** — Hindsight's three core verbs. **retain** = ingest raw
  content, LLM-extract facts/entities/relations, build a graph, embed, store (then fire
  background consolidation). **recall** = multi-strategy (semantic + BM25 + graph +
  temporal) parallel retrieval fused by RRF, reranked by a cross-encoder, truncated to a
  *token budget* — no LLM. **reflect** = an agentic reasoning loop that gathers evidence
  and returns a reasoned, cited answer shaped by the bank's disposition.
- **Observation** — Hindsight's third fact type: a deduplicated, evidence-grounded
  *belief* synthesized from multiple facts, carrying supporting-source quotes, a
  **proof count**, and a computed **freshness trend** (stable/strengthening/weakening/
  new/stale). Refined, not overwritten. This is the staleness fix hal0 lacks today.
- **Memory bank** — Hindsight's unit of isolation: one isolated store of memories,
  entities, directives, mental models. Banks share no data. Auto-created on first use.
  Maps 1:1 to hal0 namespaces.
- **Bank template** — a declarative JSON manifest (mission, disposition, entity-labels,
  mental-models, directives) that provisions a fully-configured bank in one `import`
  call. Proposed as a first-class hal0 primitive bound to personas.
- **Namespace** — hal0's memory scoping, resolved server-side from `X-hal0-Agent`:
  **`shared`** (the common brain; curated, write-gated), **`private:<agent_id>`**
  (per-agent episodic scratch; an agent reads its own + `shared`, never another's
  private), **`project:<id>`** (task/repo-scoped working sets, formalised from today's
  pass-through custom datasets), plus **`agents`** (identity cards, ADR-0011).
- **Promotion** — the gated, one-way movement of a durable Engine observation into a
  curated Wiki page, then re-embedded back into the Engine as a derived index entry. The
  seam most likely to recreate hal0's drift bugs if done wrong; hence one-way + tested.
- **Librarian** — the agent responsible for maintaining the Wiki (drafting promoted
  pages, running the nightly freshness/index/lint cycle). Proposed default:
  **Hermes-as-librarian**; open fork whether to split into a dedicated curator agent.
- **MemoryProvider** — the engine-neutral ABC/Protocol that hides the concrete engine
  (Cognee, Hindsight, or a pgvector shim) behind one contract. The linchpin: defines the
  core five methods + optional Hindsight-era methods + the `register_compiled` promotion
  seam, pinned by a conformance test suite, swapped at a single construction site.
