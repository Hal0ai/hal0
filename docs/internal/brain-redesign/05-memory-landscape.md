# 05 — Memory Landscape & Tradeoff Brief

> **Purpose.** Frame the architecture decision for hal0's "brain": how to
> combine the three memory paradigms (biomimetic vector engine, knowledge-graph
> memory, human-legible compiled wiki) plus the file-memory + RAG baseline that
> every coding agent already has, into one coherent system for a fully-local,
> self-hosted AMD Strix Halo platform.
>
> **Status.** Research brief — not a decision. Seeds a grilling session (§7).
> **Date.** 2026-06-02.
> **Author.** Memory Systems Architect (research synthesis).

---

## 0. TL;DR

- hal0 *today* runs **Cognee 1.0.7** embedded (SQLite + LanceDB + Kuzu), but
  **graph extraction and Memify are disabled by default** (ADR-0005 §6,
  ADR-0014 §1). In practice hal0 currently uses Cognee as a *plain local vector
  store* with a hand-rolled SQLite sidecar for the dataset/tag/date filters that
  Cognee 1.0's CHUNKS retriever does not honour. We are paying Cognee's
  complexity cost and getting RAG.
- The reason the graph is off is **not** laziness — it is the central
  constraint of this whole document: **Cognee's `.cognify()` pipeline depends on
  an LLM reliably emitting structured JSON, and small local models (<~32B) on a
  CPU/iGPU box cannot do that reliably.** This is independently confirmed by the
  field (see §6). hal0's own ADR-0005 §6 says exactly this.
- The strongest emerging recommendation is a **two-tier brain**: a
  machine-owned structured/semantic engine for fast recall, plus a
  **human-legible compiled wiki** (Karpathy's "LLM Wiki") as the curated source
  of truth. They are *not* redundant; they sit at different points on the
  write-cost / legibility / staleness curve.
- **Hindsight** (TEMPR + reflect + disposition + observations) is the most
  complete "biomimetic" engine and is meaningfully better-architected for the
  agent-memory job than hal0's neutered Cognee — but it is heavier (Postgres +
  pgvector + cross-encoder + an extraction LLM) and carries the *same*
  structured-extraction dependency for its richest features.

---

## 1. Taxonomy — sharp definitions

These are the building blocks. Most "memory systems" are a *bundle* of several.

| Term | One-liner | Canonical anchor |
|---|---|---|
| **File / wiki memory** | Plain markdown the agent reads *and writes* as full context; no retrieval step below the file granularity. `CLAUDE.md`, `MEMORY.md`, an Obsidian vault. | Karpathy LLM Wiki; CoALA "context". |
| **Plain RAG** | Embed a query, vector-similarity search a chunk store, stuff top-k chunks into context, generate. Stateless; re-derives knowledge every query. | CoALA semantic-via-retrieval. |
| **Vector memory** | RAG where the *agent's own past* (turns, facts) is the corpus. Persistent, append-only, similarity-recalled. | Mem0, hal0's current Cognee usage. |
| **Biomimetic memory** | Vector memory + cognitive-science structure: multi-strategy recall, entity graph, temporal reasoning, **consolidation** of raw facts into evolving *observations/beliefs*, and a reasoning pass with stable *disposition*. | **Hindsight** (TEMPR, reflect, observations). |
| **Knowledge-graph memory** | Ingestion extracts typed (subject, relation, object) triples into a graph DB *alongside* embeddings; recall does graph traversal + vector hints (multi-hop). | **Cognee** (ECL: Extract–Cognify–Load; Memify). |
| **Compiled-knowledge wiki** | The LLM incrementally maintains an interlinked markdown wiki: ingest→summary+entity/concept pages, query→answer (filed back), lint→fix contradictions. Knowledge **compounds** instead of being re-derived. | **Karpathy LLM Wiki**. |

### Cognitive-science memory types, mapped to agents (CoALA, arXiv:2309.02427)

| Type | What it stores | hal0 surface today | Natural owner |
|---|---|---|---|
| **Working / in-context** | The context window itself — system prompt, recent turns, retrieved chunks. The only memory the model directly reasons over. | The live prompt + prefetch block. | Orchestrator / dispatcher. |
| **Episodic** | Instance-specific past experiences: *what happened, when, in what order* ("I recommended X to user on date Y"). Single-shot, contextual (who/when/where/why). | `sync_turn` writes to Cognee `shared`/`private:*`. | Vector/biomimetic engine. |
| **Semantic** | Abstracted, generalized facts & domain knowledge ("user prefers functional style"). | None really — Cognee chunks are episodic-flavoured; no consolidation runs. | Engine *observations* + the wiki. |
| **Procedural** | Skills, workflows, code, "how we do X here." | `CLAUDE.md` behavioral rules; nothing dynamic. | The wiki (`schema` / playbooks). |

> The ecosystem (Letta/MemGPT, Mem0, MIRIX) has converged on this 3-tier
> (episodic/semantic/procedural) model over 2025–26, but the Dec-2025 survey
> *Memory in the Age of AI Agents* (arXiv:2512.13564) explicitly calls it "a
> starting point, not a final answer" — the **consolidation pathway**
> (episodic→semantic) and **multi-agent governance** are the open frontiers.
> Both are exactly hal0's open questions (§3, §5).

---

## 2. Comparison matrix

Scored for hal0's reality: fully local, AMD Strix Halo (iGPU + XDNA NPU via
Lemonade) for the runtime LLM, CPU elsewhere, no cloud. "Token cost at recall" =
tokens injected into the agent's context per query.

| Paradigm | Write cost | Recall latency | Human-legible | Structure | Staleness handling | Multi-agent sharing | Local feasibility | Token cost @ recall |
|---|---|---|---|---|---|---|---|---|
| **File / wiki (raw)** | Trivial (append) | Zero (it's in context) | **Excellent** | Flat→linked | **Manual** (rots silently) | Filesystem perms / git | **Trivial** | High & fixed (whole file) |
| **Compiled LLM Wiki** | **High** (LLM rewrites 10–15 pages/ingest) | Low (read index→pages) or zero (small vault in context) | **Excellent** (it's *for* humans) | Interlinked MD + graph | **Active** (lint pass flags contradictions/stale) | Git branches / page namespaces | High (needs a capable LLM to maintain) | Low–med (index + cited pages) |
| **Plain RAG** | Low (chunk + embed) | Low (1 ANN search) | Poor (chunks) | None | None (no dedup/decay) | Per-collection | **Trivial** (embeddings only) | Med (top-k chunks) |
| **Vector memory** (hal0/Cognee-as-used) | Low (embed; sidecar row) | Low (ANN + sidecar filter) | Poor | None below chunk | Weak (timestamp recency only) | Dataset namespaces ✓ (hal0 already) | **Trivial** (fastembed bge-small, 384-d) | Med (top-k) |
| **Biomimetic** (Hindsight) | **Med–High** (LLM fact-extraction at retain; async consolidation) | Med (4 strategies + RRF + cross-encoder) | Med (facts/observations readable, graph less so) | Entity + temporal + causal graph; observations | **Strong** (observations dedup/refine/decay; freshness trend; stale-verify) | Tags + banks; per-bank disposition | **Viable** local; reranker + embed local, but quality of *retain* tracks the local LLM | Med (token-budgeted by design) |
| **Knowledge-graph** (Cognee, graph ON) | **High** (LLM triple-extraction per chunk; Memify) | Med–High (graph traversal + vector) | Med (graph inspectable) | **Strong** typed graph + ontology | Med (Memify prunes/strengthens) | Per-dataset isolation ✓ | **Fragile local** — `.cognify()` needs reliable structured JSON; <32B local models produce noisy graphs (§6) | Med |

**Sharpest rows for the decision:**

1. **Write cost vs legibility are inversely correlated.** The cheapest writes
   (RAG/vector) produce the least legible artifact; the most legible artifact
   (wiki) has the most expensive write. There is no free lunch — you choose
   where to pay.
2. **Staleness is where naive vector memory dies.** Append-only vector stores
   accumulate contradictions ("user prefers React" + "user switched to Vue")
   and surface both. Only **consolidation** (Hindsight observations, Cognee
   Memify) or an **active lint pass** (wiki) resolves this. hal0 today has
   *neither* running.
3. **Local feasibility forks on one question: does the feature need the LLM to
   emit reliable structured output?** Embedding + ANN + reranking are all cheap
   and local-fine. Graph/triple extraction and rich fact-extraction are *not*
   reliable on a <32B local model. This single fact drives §4 and §6.

---

## 3. The "two-tier brain" thesis

### The claim

A coherent brain has exactly two durable tiers, plus the ephemeral working tier:

- **Tier W — working memory:** the context window. Owned by the orchestrator.
- **Tier E — the engine (machine-owned, fast, structured):** a
  vector/biomimetic store. Captures *everything* cheaply (every turn, every
  fact), recalls by similarity/graph/time, and **consolidates** episodic noise
  into semantic observations. Optimised for recall latency and recall, not for
  human reading. This is the agent's *hippocampus + association cortex*.
- **Tier C — the wiki (human-legible, curated, the source of truth):** an
  interlinked markdown corpus the LLM maintains and humans read. Optimised for
  legibility, auditability, and *compounding*. This is the agent's *published
  notebook* — the cortex's consolidated, write-up form.

### Why two tiers and not one

- **One engine only** → fast but illegible. Humans can't audit, correct, or
  trust it; staleness is invisible; "what does my agent believe?" has no
  answer a person can read. hal0's current state.
- **One wiki only** → legible but (a) write-expensive at every fact, (b)
  retrieval is page-granular (you read whole pages), and (c) it does not scale:
  Karpathy himself notes the pure-context approach wins **only below
  ~50k–100k tokens (~150–200 pages)**; past that you need an index/engine. A
  pure wiki cannot hold a year of raw conversation turns.

The two tiers are **the consolidation pathway made physical**: Tier E is where
raw episodic experience lands and gets compressed; Tier C is where the
compressed, curated semantic/procedural knowledge is published for humans.

### How they should relate (the real design questions)

1. **Which is canonical?** *Proposal:* **the wiki (Tier C) is canonical for
   curated knowledge; the engine (Tier E) is canonical for raw recall.** A
   human-readable claim in the wiki overrides a stale fact in the engine. The
   engine is canonical for "what was literally said on 2026-05-12" because the
   wiki will never hold that volume.
2. **Does the wiki get embedded into the engine?** *Proposal:* **yes, one-way.**
   Wiki pages are ingested into Tier E as high-priority documents so similarity
   recall can surface them. This mirrors Hindsight's *mental models* (curated
   summaries checked **first**) and reflect's hierarchy
   (mental models → observations → raw facts). The wiki literally becomes the
   top of the recall hierarchy.
3. **Does `reflect()`/consolidation write *to* the wiki?** *This is the crux.*
   Two postures:
   - **Conservative (recommended start):** consolidation writes *observations*
     inside Tier E only. A separate, **explicitly invoked** "compile" step (the
     wiki ingest/lint pattern) promotes durable observations into wiki pages,
     with a human in the loop. Keeps the human source-of-truth human-curated.
   - **Aggressive:** the agent auto-writes wiki pages on consolidation. Higher
     compounding, but the wiki stops being trustworthy-by-construction and
     becomes another thing to lint. Karpathy's pattern *does* file good query
     answers back as pages — but he is the reviewer in the loop.

### Counter-case (argue the other side honestly)

- **A good biomimetic engine may make the wiki redundant.** Hindsight's
  observations already *are* consolidated, deduplicated, evidence-cited,
  freshness-tracked semantic memory — and they're human-readable text. If you
  add a read-only HTML/Obsidian *view* over observations, you arguably get
  Tier C's legibility without maintaining a second store. The wiki's unique
  value then shrinks to: hand-authored procedural knowledge + interlinking +
  git history.
- **Two stores = two staleness problems = drift.** hal0 has *already been burned
  by exactly this*: the `capabilities.toml` ↔ `slots/*.toml` drift bug, the
  MEMORY.md-vs-reality drift, the `state.json` backend drift. A second
  source of truth that can disagree with the first is an operational liability
  unless the promotion direction is strictly one-way and tested.
- **YAGNI at hal0's scale.** A single-user home box may never exceed Karpathy's
  100k-token wiki threshold for *curated* knowledge. If so, the wiki *is* the
  engine, in-context, and Tier E is only needed for raw-turn recall.

**Net:** the two-tier thesis is sound, but its weakest seam is the
**promotion/consolidation direction** between tiers. Get that wrong and you
rebuild hal0's drift bugs at the brain layer. (→ §7 Q1, Q2.)

---

## 4. Where Cognee fits or exits

### Where Cognee is *now* in hal0 (grounded in source)

- Embedded: SQLite (relational) + LanceDB (vector) + Kuzu (graph), all
  file-based under `/var/lib/hal0/memory/cognee` (`cognee_wrapper.py`).
- **Graph extraction OFF by default** (ADR-0014 §1); **Memify never runs**;
  the stripped pipeline is `classify → chunk → embed` only (ADR-0005 §6,
  `_chunk_and_embed`). `LLM_API_KEY` is a noop placeholder.
- Search is `SearchType.CHUNKS` = **pure vector retrieval, no graph, no LLM**.
- A **SQLite sidecar** (`hal0_memory_index.sqlite`) shadows dataset/tags/source/
  metadata/timestamp because *"Cognee 1.0's vector retriever does not natively
  respect"* dataset isolation, tag AND-match, or date range. hal0 is
  re-implementing the filtering layer Cognee was supposed to provide.
- Namespacing (`shared` + `private:<client_id>`), audit log, and rerank are all
  hal0 code in the wrapper, *not* Cognee features.

**Blunt assessment:** hal0 is running a 3-database knowledge-graph engine to do
the job of `pgvector + a WHERE clause`, with the graph — Cognee's entire reason
to exist — switched off because it doesn't work on local models. We carry the
dependency surface (Cognee version churn, lru_cache singleton fights, the tower
of "store is empty" exceptions the wrapper catches) for a vector store.

### Why the graph is off — and why that won't change cheaply

Independently confirmed by the field (§6): Cognee's `.cognify()` leans on
Instructor/BAML **structured JSON output**. Small local models (Qwen3-4B,
Mistral-7B, even up to ~32B) fail format compliance, retry through long tenacity
windows (10+ min silent hangs reported), and produce **noisy graphs that
pollute retrieval**. Cognee's own guidance: clean graphs need **32B+** models;
the honest community verdict is "great with hundred-billion-param models, failed
on self-hosted hardware." hal0's primary slot on Strix Halo is in the
sub-32B-quantized range. **The graph hal0 disabled is the graph hal0 cannot
reliably run locally.**

### Options

**(a) Keep Cognee, turn the graph on (most "proper" if it worked).**
- *For:* multi-hop reasoning, ontology grounding, the 92.5%-vs-60% accuracy
  numbers Cognee markets.
- *Against:* requires a 32B+ structured-output-reliable LLM hal0 doesn't have
  locally; reintroduces the cognify hang/instability risk; Memify maintenance
  cost. Effectively blocked on hardware/model quality, not code.

**(b) Replace Cognee with Hindsight (recommended candidate for Tier E).**
- *For:* Hindsight is *designed* for the agent-memory job hal0 actually needs —
  TEMPR (semantic+BM25+graph+temporal+RRF+cross-encoder), **observations
  consolidation** (the staleness fix hal0 lacks), reflect with disposition, and
  a **native token-budgeted recall** (agents think in tokens — hal0 currently
  hard-caps top_k). Local-first: embeddings + reranker run local with no keys;
  llama.cpp/Ollama/LM Studio supported as the LLM provider. Single Postgres
  backend (pgvector + tsvector + recursive-CTE graph) — *fewer* moving stores
  than Cognee's three. Already integrated with **Hermes** upstream
  (`NousResearch/hermes-agent` ships a Hindsight memory plugin), which is hal0's
  bundled agent.
- *Against:* Postgres dependency (vs Cognee's embedded SQLite) — heavier than
  the current single-file story, though "local embedded" mode + a bundled PG
  daemon exists. Its richest features (`retain` fact-extraction, the graph,
  `reflect`) carry the **same** local-LLM-quality dependency as Cognee's
  cognify — but degrade more gracefully: recall works with **no LLM at all**
  (semantic+BM25+temporal+rerank are pure-local), so you get a *better vector
  store than Cognee-as-used* even before any extraction. Migration cost: move
  the `add/search/list/delete` contract from the Cognee wrapper onto
  retain/recall; re-home namespacing (Hindsight has *banks* + *tags*); re-point
  the Hermes plugin (already exists upstream).

**(c) Run both.** Cognee for graph experiments, Hindsight for production recall.
- *Against:* two engines, two staleness models, double the ops. Only justified
  if a specific graph/ontology workload demands Cognee that Hindsight's graph
  strategy can't serve. **Not recommended** for a home box.

**(d) Drop the engine to plain `pgvector` + sidecar.**
- *For:* honest about what hal0 uses today; minimal deps.
- *Against:* throws away the *upgrade path* to consolidation/observations that
  fixes staleness. You'd reinvent Hindsight's observations eventually.

### Verdict

**Replace Cognee with Hindsight as Tier E (option b), staged.** Hindsight is
strictly more aligned with hal0's needs (consolidation, token-budgeting,
disposition, Hermes integration) and degrades to "a better local vector store"
when the local LLM is too weak for extraction — which is precisely hal0's
constraint. Keep Cognee only if a concrete graph/ontology workload appears that
Hindsight's graph strategy demonstrably can't serve, and only once a 32B+
structured-output model is locally viable on Strix Halo.

> **Honesty flag.** This verdict leans on Hindsight's *self-published*
> architecture docs + the upstream Hermes plugin's existence. The
> head-to-head recall-quality claim ("better than Cognee-as-used") is an
> *architectural* judgement, not a benchmark hal0 has run. **Before
> committing, run hal0's δ/eval harness on both with the actual local primary
> model and a representative memory corpus.** (→ §7 Q5.)

---

## 5. Multi-agent memory

Actors today: **Hermes** (bundled), **external Claude Code** sessions, future
**aftermarket MCP agents** (the `/agents` MCP-host platform). hal0 already has
the right *primitive*: `shared` + `private:<client_id>` datasets, identity from
`X-hal0-Agent` (Hermes) / Bearer-extracted `client_id`, enforced in
`namespace.py` + the wrapper.

### Recommended model

- **Three namespace scopes**, not two:
  1. **`shared`** — the common brain. Both the engine's consolidated
     observations *and* the wiki live here. All trusted agents read; writes are
     **moderated** (see trust below).
  2. **`private:<agent_id>`** — per-agent scratch/episodic. The agent reads its
     own + `shared`; never another agent's private (already enforced —
     `_allowed_read_datasets` silently drops foreign `private:*`).
  3. **`project:<id>` / custom** — task- or repo-scoped working sets that
     several agents share for the duration of a job, then archive. hal0 already
     passes custom datasets through verbatim; formalise them.
- **The wiki gets the same namespacing.** `shared/wiki/`, plus optional
  per-project wikis. Git is the natural ACL + history layer for Tier C; the
  engine's dataset filter is the ACL for Tier E.

### Trust / privacy

- **Write trust is the hard part.** A shared brain that any agent can write to
  is a poisoning vector and a drift vector. *Proposal:* episodic writes to
  `shared` are cheap and allowed (append-only, attributed via `source` /
  audit log — hal0 already stamps `client_id` on every op). But **promotion
  into consolidated observations / the wiki** (the durable, trusted layer) is
  gated: either human-approved, or only from a designated agent (e.g. Hermes as
  the "librarian"), or quarantined until corroborated (Hindsight's *proof_count*
  boost is exactly this signal — observations with more independent evidence
  rank higher).
- **Privacy is already mostly right.** Keep the fail-open-empty read posture
  (foreign private → `[]`, not an error, to avoid leaking existence). Keep the
  `private:*`-by-name rejection for non-private callers (PR #366 hardening).
- **External Claude Code** is an *untrusted-ish* peer: give it a `client_id`,
  let it read `shared` + its own private, but **default it to no
  shared-observation/wiki write** until explicitly trusted. It's a guest in the
  house, not a resident.

### Namespacing recommendation (one line)

**`shared` (curated, write-gated) + `private:<agent_id>` (free, attributed) +
`project:<id>` (scoped, ephemeral)**, with identity from `X-hal0-Agent`/Bearer,
git ACLs for the wiki, dataset filters for the engine, and **promotion to the
durable layer always gated** (human or librarian-agent + proof-count
corroboration).

---

## 6. Self-host constraints

Hardware: Strix Halo iGPU + XDNA NPU + unified memory on LXC 105 (runtime LLM via
Lemonade); hal0-dev VM and most services are CPU-only; **no cloud, no NVIDIA
CUDA.** What each paradigm costs here:

| Component | Local cost | Verdict on Strix Halo |
|---|---|---|
| **Embeddings** | `bge-small-en-v1.5` (384-d, ~130MB) via fastembed/SentenceTransformers — **CPU-fast**, no GPU needed. Already hal0's default. | ✅ Trivial. |
| **Vector ANN** | LanceDB (file) today; pgvector HNSW under Hindsight. Both CPU-fine at home-box scale. | ✅ Trivial. |
| **Reranker (cross-encoder)** | `ms-marco-MiniLM-L-6-v2` (~85MB) CPU, or hal0's bundled GPU rerank slot (port 8086, `bge-reranker-v2-m3`). | ✅ Already wired. |
| **Fact / triple extraction (LLM)** | Needs the runtime LLM. **This is the bottleneck.** Strix Halo runs sub-32B quantized models; structured-output reliability is the failure mode (§4). | ⚠️ Marginal — the whole reason Cognee's graph is off. |
| **Storage** | LanceDB/SQLite (Cognee) or Postgres (Hindsight). Postgres is the only *new* daemon a Hindsight move adds. | ✅ Fine; PG can be a bundled local instance. |
| **The wiki** | Pure markdown on disk + git. **Zero infra.** Maintenance = LLM tokens at ingest/lint time. | ✅ Cheapest store, most expensive writes (LLM time). |

**Viability ranking, fully-local on this hardware:**

1. **File/wiki memory** — fully viable, near-zero infra; cost is LLM *time* to
   maintain (one capable local model, run at ingest, not per query).
2. **Plain RAG / vector memory** — fully viable; this is hal0's sweet spot
   today (CPU embeddings + ANN + optional GPU rerank).
3. **Biomimetic (Hindsight) — recall path** — fully viable local (no LLM needed
   for semantic+BM25+temporal+rerank). **Write/consolidation path** degrades
   with local LLM quality but does not *break* (it extracts fewer/rougher
   facts).
4. **Knowledge-graph (Cognee graph / Memify)** — **the only paradigm that is
   genuinely fragile fully-local**, because triple extraction *requires*
   reliable structured output that sub-32B models don't deliver. Either route
   extraction to a larger model (defeats the "no cloud" constraint) or accept
   noisy graphs.

**Key constraint, restated:** *local embedding/retrieval is free; local
structured-extraction is the scarce resource.* Architect so the brain is
**useful with zero LLM extraction** and gets *better*, not *functional*, as the
local model improves. Hindsight's recall-without-LLM property fits this;
Cognee's graph-requires-LLM property fights it.

---

## 7. Open architectural questions (grilling seeds)

1. **Promotion direction.** Is the wiki canonical and the engine derived, or the
   engine canonical and the wiki a published view? One-way promotion
   (engine→wiki) or bidirectional? Who/what triggers it — a human, a `compile`
   command, or auto-on-consolidation? *(This is the seam most likely to recreate
   hal0's drift bugs.)*

2. **Does consolidation write durable memory, or just rank it?** Hindsight
   observations *evolve beliefs* automatically. Do we trust an unsupervised
   local-LLM consolidation to mutate the shared brain, or is consolidation
   advisory (re-ranks recall) until a human/librarian promotes it?

3. **Cognee exit cost vs benefit — measured, not asserted.** Run the δ/eval
   harness: Cognee-as-used (vector) vs Hindsight recall vs plain pgvector, on
   the *actual* Strix Halo primary model and a real hal0 memory corpus. Is
   Hindsight's recall measurably better, or are we trading one dependency for
   another with no quality delta?

4. **Where does the wiki physically live and who renders it?** `shared/wiki/`
   in the hal0 data dir served read-only via the dashboard? An actual Obsidian
   vault on an NFS share? Git repo with PR-style promotion? This decides the
   human-in-the-loop ergonomics (§3 conservative posture).

5. **Local structured-output: invest or route?** Is it worth standing up a
   grammar-constrained / `tool_call`-mode extraction path on a 32B+ quantized
   model on Strix Halo to make graph/fact-extraction reliable — or do we
   permanently design the brain to *not need* reliable extraction (recall-only
   engine + hand-curated wiki)? This is the fork that decides whether the graph
   ever comes back on.

6. **Shared-write trust model.** Append-only-attributed for episodic, gated for
   durable — but *who* is the gate? Human approval doesn't scale to a chatty
   multi-agent box; a "librarian agent" centralizes a failure point;
   proof-count corroboration needs multiple independent witnesses that a
   single-user box may never get. What's the actual default?

7. **Working-memory budget ownership.** With three durable surfaces (wiki pages,
   observations, raw facts) all wanting context space, who arbitrates the
   per-turn token budget — the orchestrator, or a recall-time policy
   (mental-models→observations→facts hierarchy, hard token cap)? hal0's recall
   already hard-caps `top_k`; the budget should be *tokens*, agent-style, not
   result counts.

8. **External agents and the brain.** Should an external Claude Code session
   read hal0's shared brain at all by default? If yes, via MCP recall tools; if
   no, what's the opt-in? (Ties to the `/agents` MCP-host trust model.)

---

## Sources

- Hindsight docs (local skill `hindsight-docs`): `index.md`, `rag-vs-hindsight.md`,
  `retrieval.md` (TEMPR, RRF, cross-encoder, token budgeting), `reflect.md`
  (agentic loop, disposition, mental-models hierarchy), `retain.md`
  (fact/entity/causal extraction, consolidation), `storage.md` (Postgres +
  pgvector + tsvector + recursive-CTE graph; pg0 embedded), `models.md`
  (embeddings/reranker/LLM providers, local llama.cpp/Ollama).
- Karpathy, "LLM Wiki" gist — https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f
  (three layers: raw sources / wiki / schema; ingest–query–lint; Obsidian as
  viewer; ~50k–100k-token threshold where context beats RAG).
- Cognee architecture — https://www.cognee.ai/blog/fundamentals/how-cognee-builds-ai-memory ,
  https://memgraph.com/blog/from-rag-to-graphs-cognee-ai-memory ,
  https://www.lancedb.com/blog/case-study-cognee (ECL pipeline, Memify, ontology grounding,
  graph+vector dual store, datasets).
- Cognee local-model reliability — https://www.glukhov.org/post/2025/12/selfhosting-cognee-quickstart-llms-comparison/ ,
  https://github.com/topoteretes/cognee/issues/1812 ,
  https://github.com/topoteretes/cognee/issues/2119 (structured-output failures on <32B
  local models; 32B+ recommended; cognify hangs/instability).
- Hindsight self-host / local — https://hindsight.vectorize.io/blog/2026/03/10/run-hindsight-with-ollama ,
  https://github.com/vectorize-io/hindsight ,
  https://github.com/NousResearch/hermes-agent/blob/main/plugins/memory/hindsight/README.md
  (Hermes integration), https://www.glukhov.org/ai-systems/memory/agent-memory-providers/
  (provider footprint comparison).
- Agent memory taxonomy — CoALA, arXiv:2309.02427; *Memory in the Age of AI
  Agents: A Survey*, arXiv:2512.13564; *Episodic Memory is the Missing Piece*,
  arXiv:2502.06975; Letta/MemGPT, Mem0 (arXiv:2504.19413), MIRIX.
- hal0 source (ground truth on current usage): `src/hal0/memory/cognee_wrapper.py`
  (graph OFF, CHUNKS-only, SQLite sidecar, namespacing, rerank), `namespace.py`
  (ADR-0005 §3), `src/hal0/agents/hermes/plugins/memory_cognee/README.md`;
  ADR-0005 (Cognee engine), ADR-0014 (graph-extraction gate).
```
