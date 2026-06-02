# 06 — Proposed Architecture: hal0's Unified Brain

**Status:** Proposed architecture — opinionated, decisive, marks genuine forks for grilling.
**Date:** 2026-06-02
**Author:** AI Memory Systems Architect
**Inputs:** dossiers 01 (Hindsight) · 02 (hal0-wiki) · 03 (current-memory audit) · 04 (consumer surface) · 05 (landscape & tradeoffs).
**Supersedes (engine):** ADR-0005 (Cognee), ADR-0014 (Cognee graph gate). **Extends:** ADR-0011 (`agents` dataset), ADR-0012 (`X-hal0-Agent`, no-auth).

> This is the synthesis document the five dossiers feed into. It is written to be
> *grilled*: every load-bearing decision has a recommended default **and** the
> alternatives it beat, and §10 enumerates the decisions still genuinely open.

---

## 1. Thesis & principles

### Thesis (one paragraph)

hal0's brain is **two durable tiers behind one access plane**: a machine-owned
**Engine** (Hindsight, replacing the neutered Cognee) that captures every turn
and fact cheaply, recalls them with multi-strategy + token-budgeted retrieval,
and *consolidates* episodic noise into evidence-grounded, freshness-tracked
**observations**; and a human-legible **compiled-knowledge Wiki** (the
hal0-wiki / Karpathy LLM-Wiki layer on an Obsidian-flavored markdown vault) that
holds the curated, cross-linked, auditable knowledge a person reads, edits, and
trusts. The Engine is the hippocampus; the Wiki is the published notebook. They
relate through a **strictly one-way promotion pipeline** (Engine observation →
gated promotion → Wiki page → re-embedded back into the Engine as a
top-of-hierarchy "mental model"), so there is exactly **one canonical owner per
fact-class** and the two stores cannot silently disagree. Every consumer —
Hermes, external Claude Code, pi-coder, OpenWebUI, the dashboard — reaches both
tiers through the *unchanged* `/mcp/memory` + `/api/memory/*` contract with the
`X-hal0-Agent` namespace rule, so the redesign is felt as a deeper brain, not a
new service bolted on.

### Design principles (the constitution)

1. **Single source of truth per fact-class.** Raw episodic ("what was literally
   said on 2026-05-12") is canonical in the Engine. Curated semantic/procedural
   knowledge ("how we do X here", "the user's stable preferences") is canonical
   in the Wiki. No fact-class has two owners. This is the principle that kills
   the drift bugs hal0 has already been burned by (`capabilities.toml`↔slot,
   `MEMORY.md`↔reality, `state.json` backend drift — dossier 05 §3).

2. **No bolt-on.** The brain attaches at seams that already exist: the engine
   seam (`CogneeWrapper` → `MemoryProvider` ABC), the Hermes `prefetch()` /
   `sync_turn()` hooks, the embed + rerank capability slots, the `/mcp/memory`
   surface, the dashboard Agent→Memory tab. A consumer should get the new brain
   without bespoke glue. If a feature needs a new front door, the design is wrong.

3. **Local-first, degrade-don't-break.** The brain must be *useful with zero LLM
   extraction* and get *better* (not merely *functional*) as the local model
   improves. Embedding + ANN + rerank are free locally; reliable structured
   extraction is the scarce resource (dossier 05 §6). Architect so recall works
   with no LLM at all; route the extraction LLM to Lemonade and treat its quality
   as a tunable, not a prerequisite.

4. **Drift-resistance by construction.** Promotion is one-way and tested. The
   Wiki never writes back into the Engine as authoritative; it is re-embedded as
   a *derived index entry*. Consolidation mutates the Engine's own observations
   only. A conformance test suite pins the contract so a swap can't regress it.

5. **Provenance & gating are first-class.** Every write is attributed
   (`source`/`client_id`, already stamped). Promotion into the durable curated
   layer is *gated* (human, librarian agent, or proof-count threshold) — never an
   unsupervised local-LLM mutation of the shared brain.

---

## 2. Layered model

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  CONSUMERS                                                                     │
│  Hermes (prefetch/sync_turn)  · external Claude Code (MCP guest)               │
│  pi-coder · OpenWebUI · dashboard Agent→Memory tab · journal/events            │
└───────────────┬────────────────────────────────────────────────┬─────────────┘
                │                                                  │
                ▼                                                  ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│  (c) ACCESS PLANE  — the one front door, unchanged contract                    │
│                                                                                │
│    /mcp/memory  (FastMCP)         /api/memory/*  (REST)        /api/wiki/* (REST)│
│    memory_add/search/list/delete  add/search/list/delete       page render/list │
│    + wiki_search / wiki_get       + /graph gate (→ engine caps) + graph.json    │
│                                                                                │
│    X-hal0-Agent  →  namespace:  shared · private:<agent> · project:<id> · agents│
│    (resolved server-side in hal0.memory.namespace — clients never send dataset)│
└───────────────┬────────────────────────────────────────────────┬─────────────┘
                │  MemoryProvider ABC (the linchpin — §3)          │
                ▼                                                  ▼
┌───────────────────────────────────────────┐   ┌────────────────────────────────┐
│  (a) ENGINE  — Hindsight (Tier E)          │   │  (b) WIKI  — hal0-wiki (Tier C) │
│  machine-owned · fast · structured         │   │  human-legible · curated · canon│
│                                            │   │  -ical for curated knowledge    │
│  retain  → extract → graph → consolidate   │   │                                 │
│  recall  → TEMPR → RRF → x-encoder → budget│   │  markdown vault @ /var/lib/hal0/│
│  reflect → agentic loop + disposition      │   │  wiki  (git-backed)             │
│                                            │   │  index.md/log.md/hot.md/.manifest│
│  observations = consolidated beliefs       │   │  concepts/entities/skills/...   │
│  + freshness trend (the staleness fix)     │   │  maintained by the LIBRARIAN    │
│  banks ↔ shared / private:<id> / project:  │   │  (Hermes-as-librarian, §5)      │
│                                            │   │                                 │
│  embeddings → embed slot                   │   │  search index → POINTS AT THE   │
│  rerank     → rerank slot (:8086)          │◄──┤  ENGINE (not QMD): re-embed     │
│  extraction → lemond:13305                 │   │  changed pages as mental_models │
│  storage    → embedded Postgres (pg0)      │   │  one-way: Wiki → Engine index   │
└───────────────────────────────────────────┘   └────────────────────────────────┘
        ▲  promotion (gated, one-way) ─────────────────────────────┘
        │  Engine observation ── promote ──▶ Wiki page ── re-embed ──▶ Engine mental_model
        └───────────────────────────────────────────────────────────
```

### Layer definitions (crisp)

- **(a) Engine — Hindsight.** Episodic + semantic recall + consolidation. Owns
  raw turns/facts and the *observations* derived from them. Optimised for recall
  latency and recall quality, not human reading. **Canonical for raw episodic
  recall.** Verbs: `retain` (LLM extract → 4-edge graph → background
  consolidation), `recall` (TEMPR 4-way → RRF → cross-encoder → token budget),
  `reflect` (agentic loop with disposition). The staleness fix Cognee-as-used
  lacks lives here: observations dedup/refine/decay and carry a freshness trend.

- **(b) Compiled-knowledge Wiki — hal0-wiki.** Curated, human-legible,
  cross-linked markdown. **Canonical for curated knowledge** (procedural
  playbooks, stable semantic facts, hand-authored architecture notes, agent
  identity write-ups). No daemon — it is a directory of markdown plus skill files
  the *agent* executes. Carries provenance/confidence/lifecycle/typed-relationships
  in frontmatter. Its swappable search index points at the **Engine** (not QMD).

- **(c) Access plane.** The single front door: `/mcp/memory` (4 memory tools +
  new `wiki_search`/`wiki_get`), `/api/memory/*` (CRUD + graph gate), and a
  sibling `/api/wiki/*` for page render/list/graph. The `X-hal0-Agent` →
  namespace contract (resolved server-side in `hal0.memory.namespace`) is
  preserved verbatim. **Adding the Wiki adds *tools on the same server*, not a
  new service.**

- **(d) Consumers.** Hermes (deep, via the plugin `prefetch`/`sync_turn` seam),
  external Claude Code (first-class MCP guest), pi-coder (MCP), OpenWebUI &
  `/v1/*` (inference only, no memory by default), the dashboard Agent→Memory tab
  (the real explorer), and journal/events (candidate machine-authored producers).

---

## 3. The `MemoryProvider` interface (the linchpin)

The audit (dossier 03 §5) is unambiguous: Cognee is contained to **one file**
and **one construction site** (`api/__init__.py:1108`), but there is **no formal
ABC** — the contract is implied by call sites, MCP/REST shims, and test stubs.
Before any swap, we promote the implicit five-method contract into an explicit
`Protocol` + `ABC`, with a conformance test suite that both `CogneeWrapper` and a
new `HindsightProvider` must pass. **This is the prerequisite to everything
else.**

`src/hal0/memory/provider.py` (new):

```python
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Protocol, TypedDict, Literal, Optional, Sequence
from datetime import datetime

# ── value types (engine-neutral; shed Cognee-era leakage where possible) ──────

class MemoryItem(TypedDict, total=False):
    id: str                       # engine-stable id (NOT a fragile text-equality join)
    text: str
    dataset: str                  # shared | private:<id> | project:<id> | agents | custom
    tags: list[str]
    source: str                   # server-injected client_id (anti-impersonation)
    metadata: dict
    timestamp: str                # ISO-8601; occurrence time if known
    kind: Literal["fact", "observation", "wiki"]   # NEW: provenance of the row
    score: Optional[float]        # engines that expose a score MAY set it; consumers must not require it
    freshness: Optional[Literal["stable","strengthening","weakening","new","stale"]]  # observations only

class AddResult(TypedDict):
    id: str
    timestamp: str

class ListPage(TypedDict):
    items: list[MemoryItem]
    next_cursor: Optional[str]

class DeleteResult(TypedDict):
    deleted: int

class GraphStatus(TypedDict, total=False):
    enabled: bool
    route: str                    # upstream | primary | agent (kept for dashboard contract)
    provider: str
    model: str
    nodes: int
    links: int
    pending_consolidation: int    # NEW: surfaces Hindsight /stats counters

Mode = Literal["vector", "graph", "hybrid"]   # kept for back-compat; engines map as they can


# ── the contract every engine must satisfy ───────────────────────────────────

class MemoryProvider(ABC):
    """Engine-neutral durable-memory contract. Cognee, Hindsight, and a plain
    pgvector shim are all implementations. Hidden behind this so the access
    plane, dispatcher, CLI, and Hermes plugin never import a concrete engine."""

    # ---- core five (the existing implied contract, now formalised) ----
    @abstractmethod
    async def add(self, text: str, *, dataset: str, tags: Sequence[str] = (),
                  source: str, metadata: Optional[dict] = None,
                  client_id: Optional[str] = None) -> AddResult: ...

    @abstractmethod
    async def search(self, query: str, *, limit: int = 10,
                     dataset: str | Sequence[str] = "shared",
                     tags: Sequence[str] = (),
                     before: Optional[datetime] = None,
                     after: Optional[datetime] = None,
                     mode: Mode = "vector",
                     client_id: Optional[str] = None) -> list[MemoryItem]: ...

    @abstractmethod
    async def list_items(self, *, dataset: str = "shared",
                         cursor: Optional[str] = None, limit: int = 50,
                         client_id: Optional[str] = None) -> ListPage: ...

    @abstractmethod
    async def delete(self, ids: Sequence[str], *,
                     client_id: Optional[str] = None) -> DeleteResult: ...

    # ---- runtime-flip helpers (dashboard graph-gate contract; keep) ----
    @abstractmethod
    async def graph_status(self) -> GraphStatus: ...
    @abstractmethod
    async def set_graph_enabled(self, enabled: bool, *, route: Optional[str] = None,
                               provider: Optional[str] = None,
                               model: Optional[str] = None) -> GraphStatus: ...
    @abstractmethod
    async def set_rerank_enabled(self, enabled: bool) -> None: ...

    # ---- NEW capability surface the Hindsight era unlocks (optional-by-default) ----
    async def recall(self, query: str, *, dataset: str | Sequence[str] = "shared",
                     max_tokens: int = 1024, types: Sequence[str] = ("observation","world","experience"),
                     client_id: Optional[str] = None) -> list[MemoryItem]:
        """Token-budgeted, observation-aware recall. Default impl == search()
        clipped to a count; Hindsight overrides with true TEMPR token-budgeting."""
        return await self.search(query, dataset=dataset, client_id=client_id)

    async def reflect(self, query: str, *, dataset: str = "shared",
                      max_tokens: int = 2048, response_schema: Optional[dict] = None,
                      client_id: Optional[str] = None) -> dict:
        """Reasoned answer + citations. NotImplemented on engines without a
        reasoning loop (pgvector shim raises; consumers feature-detect)."""
        raise NotImplementedError

    async def consolidate(self, *, dataset: str = "shared") -> dict:
        """Force consolidation now (else background). No-op on engines without it."""
        return {"observations_created": 0, "observations_updated": 0}

    # ---- the promotion seam (§6) — the one method the Wiki layer calls ----
    async def register_compiled(self, *, page_id: str, text: str, dataset: str,
                               tags: Sequence[str], source: str = "wiki") -> AddResult:
        """Re-embed a compiled Wiki page into the engine as a top-of-hierarchy
        index entry (Hindsight: a mental_model / kind='wiki' document). One-way:
        the Wiki is canonical; this is a derived index, never read back as truth.
        Default impl == add(..., tags=[*tags, 'wiki']) so even pgvector supports it."""
        return await self.add(text, dataset=dataset, tags=[*tags, "wiki"],
                              source=source)
```

Notes that make this real, not aspirational:

- **The core five stay byte-compatible** with what `CogneeWrapper` exposes today
  (dossier 03 §5), so the MCP server (`mcp/memory.py`), REST shims
  (`routes/memory.py`), in-process dispatcher, and the Hermes plugin do **not**
  change their call sites — only the construction site at `api/__init__.py:1108`
  swaps `CogneeWrapper()` → `provider_from_config(cfg)`.
- **`MemoryItem.id` becomes the join key**, retiring Cognee's fragile
  text-equality sidecar join (dossier 03 §4.3). A Hindsight memory id *is* the
  stable id; no shadow store needed.
- **The optional methods** (`recall`, `reflect`, `consolidate`,
  `register_compiled`) are where the Hindsight upgrade lands. They have safe
  defaults so a `PgVectorProvider` fallback (dossier 05 §4 option d) still
  satisfies the ABC.
- **Conformance suite** (`tests/memory/test_provider_contract.py`, new): a single
  parametrized suite run against every provider — `CogneeWrapper`,
  `HindsightProvider`, `PgVectorProvider`, and the test fakes — asserting
  namespace isolation, tag-AND, date-range, delete semantics, and the
  fail-open-empty foreign-private read. This is what de-risks the swap (dossier
  03 §6 "no formal engine interface").

---

## 4. Engine decision — Hindsight, wired locally

**Recommendation: replace Cognee with Hindsight as Tier E (dossier 05 §4 option
b), staged behind the `MemoryProvider` ABC.** Hindsight is *designed* for the job
hal0 actually needs (consolidation/observations = the staleness fix, token-
budgeted recall, disposition, an upstream Hermes plugin) and — critically —
**degrades to "a better local vector store than Cognee-as-used" with no LLM at
all** (semantic + BM25 + temporal + rerank are pure-local). That degrade story is
exactly hal0's local-first constraint (principle 3).

### Local wiring on hal0 (CT 105)

| Concern | Decision | Why |
|---|---|---|
| **Service placement** | Run `hindsight-api` (full image, `:8888` API; `:9999` control-plane optional) as a systemd unit on **CT 105** next to lemond, alongside `registry/` and `lemonade/`. Data root `/var/lib/hal0/memory/hindsight/`. | Co-locate the brain with the runtime that maintains it. Single LXC, no new host. |
| **Postgres** | **Embedded `pg0`** (Hindsight default; `~/.hindsight/data` → bind to `/var/lib/hal0/memory/hindsight/pg0`). NOT a separate Postgres daemon. | pg0 is the only *new* moving part vs Cognee's three embedded stores — and it replaces all three (dossier 05 §4b). `$HAL0_HOME` snapshot covers it. |
| **Embeddings** | Route to the **embed capability slot** via Hindsight's self-hosted **TEI / OpenAI-compatible embedding provider**, NOT the bundled BGE. Point `HINDSIGHT_API_EMBEDDING_*` at the embed slot (consider bge-on-iGPU, `PLAN.md:1088`). | Dossier 04 §4: embed is a real slot. **Do not bundle a second embedder.** If the embed slot's model ≠ bge-small-384, re-embed on cutover. |
| **Reranker** | Route to the **rerank slot on :8086** (`bge-reranker-v2-m3`) via Hindsight's external/TEI reranker provider — the same slot `CogneeWrapper` already POSTs to (`cognee_wrapper.py:209`). | Dossier 04 §4 / dossier 03 §1.9 step 6: already wired. Reuse, don't duplicate. Fallback: Hindsight's local MiniLM cross-encoder (CPU) or `rrf` (no neural rerank). |
| **Extraction LLM** | `HINDSIGHT_API_LLM_PROVIDER=openai`-compatible → **`HINDSIGHT_API_LLM_BASE_URL=http://127.0.0.1:13305`** (lemond gateway, dossier — Lemonade gateway port 13305). NOT Hindsight's bundled llamacpp/Gemma. | Inference lives in Lemonade on the iGPU/NPU; Hindsight's bundled llamacpp would be a second, CPU-only inference path. Caveat: arbitrary models need ≥65k output-token support or set `RETAIN_MAX_COMPLETION_TOKENS` lower (> `RETAIN_CHUNK_SIZE` 3000). |
| **Strix-Halo accel** | **Assume CPU** for embed/rerank (Hindsight docs never mention ROCm/XDNA — dossier 01 §8.2 Gap 1). Validate empirically; the rerank slot already gives GPU rerank for free. | Don't assume the iGPU accelerates Hindsight's PyTorch/ONNX models. The only GPU-sensitive read-path component (rerank) is offloaded to the slot. |
| **MCP** | Hindsight's **built-in MCP server** (`/mcp/{bank_id}/`) is NOT exposed directly. hal0's `/mcp/memory` stays the front door; `HindsightProvider` calls Hindsight over its REST/SDK in-process. | Preserve the access-plane contract (principle 2). Optionally register Hindsight's MCP as an aftermarket server later for power users. |

### Bank ↔ namespace mapping

Hindsight **memory banks** are the unit of isolation; hal0 has **datasets**.
Map them 1:1 so the `X-hal0-Agent` rule (dossier 03 §2.1) is preserved:

| hal0 namespace | Hindsight bank | Notes |
|---|---|---|
| `shared` | bank `shared` | The common brain. Observations + re-embedded Wiki pages live here. |
| `private:<agent_id>` | bank `private__<agent_id>` (`:` → `__`, Hindsight bank-id-safe) | Per-agent episodic. `_allowed_read_datasets` still unions `shared` + own private. |
| `project:<id>` | bank `project__<id>` | Formalised from today's pass-through custom datasets (dossier 05 §5). Auto-created on first use. |
| `agents` | bank `agents` | Identity cards (ADR-0011). |

`HindsightProvider.add(dataset=…)` resolves the bank, `search` recalls from the
allowed banks. Hindsight tags (`user:`, `session:`, `topic:`) carry hal0's
`tags`; Hindsight's hard SQL `tag:true` entity-label filter replaces the sidecar
tag-AND filter — **the engine owns its own filtering** (dossier 03 §6 directive:
"own its own filtering, retiring the sidecar").

### Degrade / fallback ladder

1. **Hindsight + Lemonade extraction healthy** → full retain/recall/reflect +
   observations. Best case.
2. **Lemonade unreachable / extraction model too weak** → set
   `HINDSIGHT_API_LLM_PROVIDER=none` at runtime; `retain` stores facts without
   extraction, `recall` is semantic+BM25+temporal+rerank (still better than
   Cognee-as-used). **Brain stays useful** (principle 3).
3. **Hindsight unavailable at boot** (dossier 04 §8 — the whole brain surface is
   conditionally mounted today) → `provider_from_config` falls back to a
   `PgVectorProvider` or a no-op provider; `/mcp/memory` still mounts, tools
   return `available:false`. The dashboard renders a "no engine" state.

> **Cutover discipline (dossier 05 §4 honesty flag + §7 Q3):** before flipping
> the construction site, run hal0's δ/eval harness on **Cognee-as-used vs
> Hindsight-recall vs plain-pgvector**, on the *actual* Strix-Halo primary model
> and a representative corpus. The architectural judgement that Hindsight recalls
> better is not yet a benchmark. Ship the ABC + conformance suite + dual-write
> migration first; flip the default only after the eval.

---

## 5. Wiki layer design

### Where the vault lives

**`/var/lib/hal0/wiki`** on CT 105, alongside `registry/`, `lemonade/`,
`memory/hindsight/`. It is *just a directory of markdown* — no service to host
(dossier 02 §7.3). Backed by **git** (`Obsidian Git` convention → a git repo with
a periodic commit; btrfs snapshot is belt-and-suspenders). `OBSIDIAN_VAULT_PATH`
in `~/.obsidian-wiki/config` for the agent user points here.

### Who maintains it

**Hermes-as-librarian** (dossier 02 §7.2, dossier 05 §5 "librarian agent").
Rationale: Hermes is the bundled long-running agent, `~/.hermes/skills/` install
is already supported, `.hermes.md`→`AGENTS.md` is wired, and it already holds the
`memory-curator` role in its identity card (dossier 04 §1.1.5). Concretely:

- `obsidian-wiki setup` installs the wiki skills into `~/.hermes/skills/` at
  Hermes provision time.
- A **systemd timer** (the framework's macOS launchd plist → a CT 105 timer) runs
  `daily-update` nightly: freshness pass, `index.md`/`hot.md` rebuild, lint.
- The two portable skills `wiki-update` (write) / `wiki-query` (read) let *any*
  hal0 agent push to / read from the same vault, with agent-of-origin tracked in
  `.manifest.json` for `memory-bridge` attribution.

> **Open fork (→ §10 Q3):** Hermes-as-librarian centralises a failure point and a
> trust point. Alternative: a dedicated lightweight "librarian" persona/agent
> whose *only* job is curation, separable from Hermes's conversational role. Start
> with Hermes; split if curation contends with conversation.

### How it's viewed

CT 105 is headless; Obsidian is a desktop app. Three viewing paths, ship in order:

1. **Dashboard render (primary).** Surface the vault read-only through the hal0
   dashboard Agent→Memory tab (the SPA already serves on `:8080`). Render markdown
   + the `wiki-export` `graph.html`/`graph.json` (a server-free graph view the
   framework already produces). This is the "feels native" path.
2. **Git remote → Obsidian on a desktop** (`hal0-dev` VM or a user machine over
   NFS/devpool). For deep human editing.
3. **Obsidian-over-RDP** to the desktop VM. Heaviest; only if 1+2 insufficient.

### How the wiki's search index points at the Engine

This is the single biggest "Hal0'd" change to the fork (dossier 02 §7.5). The
framework's own answer is **QMD** ("the markdown vault is the source of truth;
QMD is a search index, not the source of truth"; every write skill refreshes QMD
after the vault write). **We swap QMD for the Engine**, preserving the exact
semantics:

- On every vault write (`wiki-ingest`, `wiki-update`, lint), the maintaining
  agent calls **`register_compiled(page_id, text, dataset, tags)`** (§3) — which
  on Hindsight creates/updates a `kind='wiki'` document / **mental_model** in the
  `shared` bank. One-way: Wiki → Engine index.
- On query, `wiki-query` does a **semantic pass against the Engine first**
  (`recall` with `types=['wiki','observation']`), then falls back to Grep/Glob
  over the vault (the framework's graceful-degrade path is preserved).
- The fork edit is mechanical: the skills hard-reference `qmd` commands; replace
  those call sites with `/api/wiki`-mediated `register_compiled` / `recall` calls.
  This is the deliberate divergence to plan now while the fork is byte-identical
  to upstream (dossier 02 §6).

### The role of `memory-bridge`

`memory-bridge` (dossier 02 §3.6, "the killer feature") diffs wiki knowledge by
**which AI tool produced it** (reads `.manifest.json` `source_type` + page
`sources:`). This lines up with hal0-memory's per-agent dataset namespacing: the
dashboard can render **"what does Hermes know that Claude-Code doesn't"** and
surface cross-agent blind spots. It is the legible, human-facing counterpart to
the Engine's per-bank isolation — keep it, wire its attribution to the
`X-hal0-Agent` identity that already stamps every Engine write.

---

## 6. The promotion model (CRITICAL)

This is the seam most likely to recreate hal0's drift bugs (dossier 05 §3 "weakest
seam", §7 Q1/Q2). We resolve it decisively, with alternatives marked.

### Canonical ownership (the rule that prevents drift)

| Fact-class | Canonical owner | Rationale |
|---|---|---|
| Raw episodic ("what was said on date X", turn transcripts) | **Engine** | The Wiki will never hold a year of raw turns (Karpathy ~100k-token ceiling, dossier 05 §3). |
| Engine-derived **observations** (auto-consolidated beliefs) | **Engine** | They evolve automatically; mutating them by hand defeats consolidation. |
| Curated semantic facts ("user's stable preferences"), procedural playbooks ("how we do X here"), architecture notes, agent identity write-ups | **Wiki** | Human-legible, audited, git-historied. A human-readable Wiki claim **overrides** a stale Engine fact at recall time. |

### Recommended default: **gated one-way promotion, Engine → Wiki, Wiki → Engine-as-index**

```
 episodic turn ──retain──▶ Engine fact ──background consolidate──▶ Engine observation
                                                                        │
                                              proof_count ≥ N OR freshness=='stable'
                                                                        │  (PROMOTION CANDIDATE)
                                                                        ▼
                                                          ┌─────────────────────────┐
                                                          │  PROMOTION GATE          │
                                                          │  (default: librarian     │
                                                          │   agent reviews; human   │
                                                          │   approves via dashboard │
                                                          │   inbox for shared/      │
                                                          │   project; auto for      │
                                                          │   private:<agent>)       │
                                                          └────────────┬────────────┘
                                                                       │ approved
                                                                       ▼
                                                        Wiki page (wiki-ingest writes,
                                                        provenance=inferred, lifecycle=draft)
                                                                       │
                                                       register_compiled() (one-way)
                                                                       ▼
                                              Engine mental_model / kind='wiki' (top of
                                              recall hierarchy: wiki → observations → facts)
```

- **Consolidation writes to the Engine only** (its own observations). It does
  **not** auto-write the Wiki. (Dossier 05 §3 "conservative posture", recommended
  start.)
- **Promotion into the Wiki is gated.** Default gate by namespace:
  - `private:<agent>` → **auto-promote** (it's the agent's own scratch; low blast
    radius).
  - `shared` / `project:<id>` → **gated**: a promotion candidate (observation with
    `proof_count ≥ N` *or* `freshness == 'stable'`) is queued; the **librarian
    agent** drafts the Wiki page (`provenance: inferred`, `lifecycle: draft`); a
    **human approves** via the dashboard inbox (reusing the existing
    destructive-call approval-queue UX, dossier 04 §3.1) to flip
    `lifecycle: reviewed`.
- **Wiki → Engine is one-way and derived.** `register_compiled` re-embeds the page
  as a `kind='wiki'` index entry. The Engine never treats it as a mutable
  truth-source; it's the top of the recall hierarchy (mirroring Hindsight's
  *mental models checked first*, dossier 01 §1.2 reflect hierarchy). This is the
  single-source-of-truth-per-fact-class principle made physical: the Wiki page is
  canonical; the Engine copy is an index.

### Alternatives (honest forks)

- **(Alt-A) Aggressive auto-promotion.** Agent auto-writes Wiki pages on every
  consolidation. *For:* maximum compounding, zero human latency. *Against:* the
  Wiki stops being trustworthy-by-construction and becomes another thing to lint;
  reintroduces a second unsupervised-mutation source of truth (dossier 05 §3). Use
  only for `private:<agent>` banks, never `shared`.
- **(Alt-B) Wiki-canonical-for-everything, Engine purely derived.** The Wiki is
  the sole source of truth; the Engine is a disposable index rebuilt from the
  vault. *For:* one source of truth, period; YAGNI for a single-user box that may
  never exceed the 100k-token ceiling (dossier 05 §3 counter-case). *Against:*
  throws away cheap episodic capture (a year of raw turns can't live in markdown);
  loses the consolidation upgrade path.
- **(Alt-C) Proof-count-only auto-gate (no human).** Promote when `proof_count ≥
  N` with no human review. *For:* scales without a human bottleneck. *Against:* a
  single-user box may never get N independent witnesses (dossier 05 §7 Q6); N=1
  degenerates to Alt-A.

### Main risk of the recommended model

**The gate is a bottleneck or a rubber stamp.** If the human never reviews the
promotion inbox, `shared` knowledge ossifies as draft-only and the Wiki's
curated layer stays thin — the brain quietly degrades to "Engine + an empty
notebook". Mitigation: make the librarian agent's draft *good enough to one-click
approve*, default `private:<agent>` to auto (most volume), and treat an aging
promotion inbox as a dashboard health signal. This risk is explicitly carried
into §10 for grilling.

---

## 7. Deep-integration map (per consumer)

The rule (dossier 04 §6): "native" = a consumer gets memory/wiki without bespoke
glue. Hook at seams that already exist.

| Consumer | Exact hook | What changes |
|---|---|---|
| **Hermes — recall** | `Hal0CogneeProvider.prefetch(query)` (`provider.py:137`) — the live per-turn injection seam (dossier 04 §1.1). | `prefetch` calls `recall(types=['wiki','observation','world'], max_tokens=…)` instead of flat `search(limit=5)`. Returns Wiki-block + observation-block, top-of-hierarchy first. Rename plugin `hal0-cognee` → `hal0-memory`. |
| **Hermes — write** | `sync_turn(user, assistant)` (`provider.py:163`) + `on_memory_write` (`:207`). | Unchanged call path → `retain` (was `add`). Background consolidation now actually runs (it didn't on Cognee). Fix the **latent wrong-route bug** in `_client.py` list/delete (dossier 03 §4.2) while here. |
| **Hermes — context files** | `HERMES.md.j2` / `AGENTS.md.j2` via `_phase_context_link` (`hermes_provision.py:1230`, dossier 04 §1.1.3). | Add a "Wiki index / how to query the brain" section so any agent landing in `/etc/hal0` learns the brain exists. |
| **External Claude Code** | `/mcp/memory` MCP guest (dossier 04 §1.2, §3.3) — first-class external consumer. | Add `wiki_search`/`wiki_get` tools on the *same* `hal0-memory` server. Default an external client to **read `shared`+own-private, no shared-write** (dossier 05 §5 trust). Ship the six-lifecycle-hook ingestion plugin (SessionStart/UserPromptSubmit/PostToolUse/Stop/PreCompact/SessionEnd) so it *produces* into the Wiki, not just reads — the headline "every agent feeds one brain" story (dossier 04 §6 Tier C-8). |
| **pi-coder** | `pi-mcp-adapter.json` → `/mcp/memory` (`pi_coder.py:123`). | Wiki tools appear automatically (MCP). Mine the `pi-memory-md` precedent (per-project markdown coexisting with central store, dossier 04 §1.3) as the `project:<id>` Wiki-namespace analogy. |
| **OpenWebUI / `/v1/*`** | Inference only; **no memory by default** (dossier 04 §2, §7). | No change. Memory is an agent concern, not a raw-inference one. (Optional later: a memory-augmented chat persona.) |
| **Dashboard Agent→Memory tab** | `ui/src/dash/agents/memory-tab.jsx` — the real explorer (dossier 04 §5; stats currently **mock** `2,847`). | Wire real stats (Hindsight `get_bank_stats` + `/stats` pending-consolidation). Add an **Obsidian Wiki browser** (render markdown + `graph.html`) and the **promotion inbox** (reuse approval-queue UX). Keep the GraphExtractionPanel as the engine graph-gate. **Do NOT** confuse with the hardware "Memory map" (`memory-map.jsx`) — name collision only. |
| **embed + rerank slots** | `capabilities/catalog.py` `embed`/`rerank` (dossier 04 §4). | Hindsight embeddings → embed slot; rerank → :8086 slot. Consider a **`memory` capability card** in `capabilities.toml` so the brain's embed/rerank wiring + health render in the same UX as voice/img cards. |
| **journal / events** | `src/hal0/journal/`, `src/hal0/events/` (dossier 04 §4 adjacent). | **Optional, gated:** a producer that ingests operational events ("model X loaded", "slot evicted") into `shared` as machine-authored memory tagged `["machine","event"]`, so the brain knows the box's history. Off by default (volume/noise). |
| **Webhooks** | Hindsight `consolidation.completed` / `retain.completed` (dossier 01 §6). | Point `HINDSIGHT_API_WEBHOOK_URL` at a hal0 endpoint that fans `consolidation.completed` into the **EventBus** (footer/journal live update) and **enqueues promotion candidates**. Dedupe on `operation_id` (at-least-once). Verify per-bank registration (dossier 01 Gap 2). |

---

## 8. Bank-templates as a hal0 primitive

The user's interest in templates maps cleanly: hal0 already drives capability and
slot config from **TOML templates**; Hindsight ships **JSON bank-templates**
(dossier 01 §3) that provision a fully-configured bank (mission, disposition,
entity-labels, mental-models, directives) in one `import` call. Treat a
**"memory bank template" as a first-class hal0 primitive**, one per persona/project:

- **Location:** `/etc/hal0/memory-templates/<id>.json` (echoing
  `/etc/hal0/mcp-servers/<id>.toml` and `capabilities.toml`). hal0 owns a curated
  set, contributable like slot templates.
- **Binding to personas (dossier 04 §1.1.4):** a Hermes persona already scopes a
  memory namespace; extend it so a persona *names a memory-bank template*. When a
  persona is selected, hal0 ensures the matching bank exists by importing the
  template (idempotent — Hindsight matches mental-models by `id`, directives by
  `name`).
- **Curated hal0 templates** (seed set, from Hindsight's Hub + hal0 needs):
  `coding-agent` (high literalism, project-architecture mental-models — for
  pi-coder / Claude-Code project banks), `conversation` (Hermes default persona),
  `personal-assistant`, and a hal0-specific `homelab-ops` (entity-labels for
  hosts/slots/services; directives encoding the inference policy).
- **Round-trip / portability:** `export` emits only explicit overrides → a
  persona's memory personality becomes a portable, version-controlled artifact in
  the repo (`docs/` or `installer/`), `import --dry-run` validates it in CI.
- **Caveat to verify (dossier 01 Gap 3):** the template `entity_labels` form
  (flat `string[]`) vs bank-config rich label-group objects, and
  `retain_extraction_mode: chunks` only in the template schema. Confirm what the
  importer accepts before shipping rich-label templates.

This gives hal0 the same "stamp a known-good config from a template" ergonomics
for *memory* that it has for *slots*, and makes per-agent memory personality
declarative and reviewable.

---

## 9. What we keep / change / delete vs the current Cognee system

### Keep (the plumbing is solid — dossier 03 §6 strengths)

- **The single narrow seam** (one construction site) — *promote* it to a formal
  `MemoryProvider` ABC (§3) but keep the swap-at-one-site property.
- **`hal0.memory.namespace`** (the shared resolver that killed #317 drift) —
  unchanged; it now maps to Hindsight banks (§4).
- **The dual transport contract** `/mcp/memory` + `/api/memory/*` with the
  `X-hal0-Agent` server-side namespace rule — verbatim.
- **Defensive transport layer:** hand-rolled validation, server-injected
  `source`, `private:` rejection, consistent error envelopes, best-effort agent
  hooks that never wedge the loop, the audit trail.
- **Rerank slot integration** (:8086) — reuse from Hindsight.
- **The graph-status dashboard contract** (`graph_status()` payload shape) — kept
  in the ABC so the GraphExtractionPanel keeps working.

### Change

- **Engine: Cognee → Hindsight** behind the ABC, staged, eval-gated (§4).
- **Hermes plugin `hal0-cognee` → `hal0-memory`**; `prefetch` → `recall`,
  `add` → `retain`; fix the latent wrong-route list/delete bug (dossier 03 §4.2).
- **Filtering ownership: sidecar SQLite → the engine.** Hindsight owns dataset
  isolation, tag-AND (via `tag:true` entity labels), and date range natively
  (dossier 03 §6 directive). The fragile text-equality join (dossier 03 §4.3) dies
  with it.
- **hal0-wiki fork: QMD → Engine** as the search index; vault at
  `/var/lib/hal0/wiki`, Hermes-as-librarian, git-backed (§5). Rename
  `obsidian-wiki` → `hal0-wiki` in `pyproject.toml` (currently still upstream's,
  dossier 02 §6/§7.5).
- **Dashboard Memory tab:** real stats + Wiki browser + promotion inbox (§7).
- **Personas:** gain a `memory_bank_template` field (§8).

### Delete

- **Cognee** (`cognee==1.0.7`) and `cognee_wrapper.py` once the eval clears and
  Hindsight is default. Mark **ADR-0005 / ADR-0014 superseded.**
- **The sidecar `hal0_memory_index.sqlite`** and its text-equality join (dossier
  03 §2.2, §4.3) — migrate its rows into Hindsight on cutover, then drop.
- **The write-only Kuzu graph** (dead weight — dossier 03 §4.5): never queried;
  Hindsight's graph is read in TEMPR, so this is a net upgrade, not a loss.
- **The pre-existing local `obsidian-vault` skill** on hal0-dev (flat `/mnt/d/...`
  WSL path, no provenance — dossier 02 §7.3) — retire to avoid two competing
  Obsidian conventions.
- **Stale auto-memory entry** `hal0_memory_dataset_namespace_bug` — #317 is fixed
  (dossier 03 §4.1); mark resolved.

---

## 10. Risks & open questions (grilling seeds)

These are genuinely unresolved or carry real downside; do not paper over them.

1. **Promotion gate = bottleneck or rubber stamp (§6 main risk).** If the human
   never works the promotion inbox, `shared` curated knowledge ossifies as draft.
   Is `private:<agent>`-auto + `shared`-gated the right split? What's the actual
   default trust gate on a single-user box where proof-count corroboration may
   never accrue (dossier 05 §7 Q6)?

2. **Eval not yet run (dossier 05 §4 honesty flag, §7 Q3).** The "Hindsight recalls
   better than Cognee-as-used" claim is architectural, not benchmarked. The whole
   engine swap is conditional on the δ/eval result on the *actual* Strix-Halo
   primary model. If the delta is null, do we still pay the Postgres/Hindsight
   migration cost for the consolidation/observations *upgrade path* alone?

3. **Librarian centralisation (§5).** Hermes-as-librarian is a single failure +
   trust point. Split into a dedicated librarian persona/agent, or accept the
   coupling? When does curation contend with Hermes's conversational role?

4. **Strix-Halo accel is undocumented (dossier 01 Gap 1).** We *assume* CPU
   embed/rerank and offload rerank to :8086. Unvalidated. If CPU embed is too slow
   at ingest volume, is bge-on-iGPU (the embed slot) actually faster, and does it
   change the embedding model (forcing a re-embed)?

5. **Local structured-output: invest or route (dossier 05 §7 Q5)?** We route
   extraction to lemond:13305 and design the brain to not *need* reliable
   extraction. But Hindsight's *richest* features (graph edges in TEMPR, good
   observations) still track local-LLM quality. Do we ever stand up a
   grammar-constrained / tool-call extraction path on a 32B+ quantized model, or
   permanently design for recall-only + hand-curated Wiki?

6. **Working-memory budget ownership (dossier 05 §7 Q7).** Three durable surfaces
   (Wiki pages, observations, raw facts) compete for context space. Does `recall`'s
   token budget + the wiki→observations→facts hierarchy arbitrate it, or does the
   orchestrator? Where does the per-turn cap live?

7. **External-agent default read (dossier 05 §7 Q8).** Should an external Claude
   Code session read hal0's `shared` brain by default? We propose read-yes /
   shared-write-no. Is even read-yes too much for an untrusted LAN guest?

8. **Hindsight bundled-daemon port collision (dossier 01 §9.2).** The Hermes
   *upstream* Hindsight plugin's `local` mode auto-embeds a daemon on `:9077`,
   which collides conceptually with hal0's port map. We point Hermes at the
   standalone CT-105 Hindsight API instead — confirm the upstream plugin lets us
   override the URL without spawning its own daemon.

9. **Webhook per-bank registration (dossier 01 Gap 2).** The promotion-candidate
   fan-out depends on `consolidation.completed`. The local skill documents only
   server-wide env-var webhook config; per-bank registration may not exist. If
   it's server-wide only, promotion-candidate routing must demultiplex by `bank_id`
   in one handler.

10. **Migration data fidelity.** Cutover must move sidecar rows + Cognee chunks
    into Hindsight banks without losing the `dataset`/`tags`/`timestamp`/`source`
    schema (dossier 03 §2.2). Dual-write window, or one-shot backfill + verify? The
    text-equality join means some Cognee rows may not cleanly map — what's the
    acceptable loss?

11. **YAGNI counter-case (dossier 05 §3, Alt-B in §6).** A single-user home box
    may never exceed Karpathy's ~100k-token curated-knowledge ceiling. If so, the
    Wiki *is* the brain in-context and the Engine is only for raw-turn recall —
    arguably we don't need the full Hindsight consolidation machinery. Is the
    two-tier design over-built for v1?
```
