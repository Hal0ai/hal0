# 08 — Grilling Decision Tree

> **Purpose.** This is the working document for the post-reboot grilling session
> (`/grill-me` / `/grill-with-docs`). It collects every genuinely-open design
> decision surfaced by the research wave (docs 01–07) and arranges them as a
> **dependency-ordered tree**: resolve the roots first, because their answers
> prune or reshape the branches below them.
>
> Each node carries: **Question · Why it matters · Options · Depends on ·
> Recommended answer · Grill focus** (the sharpest attack on my own
> recommendation — start the interrogation there).
>
> Status: planning, 2026-06-02. Nothing here is decided. Recommendations are
> *opening positions to be stress-tested*, not conclusions.

---

## How to read this tree

```
ROOT  S — Scope & commitment        (decide first; gates everything)
 ├── A — Engine                     (what powers recall)
 ├── B — Namespace & multi-agent    (who sees what)
 ├── C — Wiki layer                 (the human-legible tier)
 ├── D — Promotion model            (how the two tiers relate) ← the crux
 ├── E — Integration mechanics      (the wiring)
 └── F — Process & sequencing
```

Dependency order for the session: **S → A → B → D → C → E → F.**
(D before C is deliberate: how the tiers relate determines what the wiki layer
even needs to be. If D collapses to "engine-only", branch C largely evaporates.)

---

## ROOT — S. Scope & commitment

### S1. Are we replacing the memory engine at all, or hardening what exists?
- **Why it matters.** Everything downstream assumes a swap. If the answer is
  "harden Cognee," the entire roadmap collapses to a much smaller refactor.
- **Options.**
  1. **Replace** the engine (Hindsight candidate) behind a clean seam.
  2. **Reduce** — drop Cognee's dead graph layer, keep a thin `pgvector + filters`
     store, no new dependency.
  3. **Harden Cognee** in place (turn on graph with a 32B+ extractor).
- **Recommended answer.** **Option 1 (replace), but gated by the eval in A3** —
  with Option 2 as the *fallback the seam gives us for free*. Rationale (doc 03/05):
  hal0 already runs Cognee as "pgvector + a fragile SQLite text-join sidecar"
  with the graph off; Hindsight's *observations/consolidation* directly fixes the
  staleness gap nothing in hal0 addresses today, and it degrades to "a better
  local vector store" even with no extraction LLM.
- **Grill focus.** "You're adding a Postgres daemon and a whole new engine to fix
  staleness — but the cheapest staleness fix is a wiki-lint pass you're building
  anyway in branch C. Why isn't Option 2 + wiki the whole answer?"

### S2. Two-tier brain (engine **+** wiki), or single-tier?
- **Why it matters.** This is the YAGNI question (architect Q11). Two stores = two
  staleness sources = drift risk — and hal0 has *already been burned* by drift
  (capabilities.toml↔slot, MEMORY.md). The wiki only earns its keep if it's
  doing something the engine genuinely can't.
- **Options.**
  1. **Two-tier** — engine for raw recall, wiki for curated human-canonical knowledge.
  2. **Engine-only** — Hindsight observations *are* the legible layer (they're
     deduped, cited, freshness-tracked, human-readable).
  3. **Wiki-only** — Karpathy-pure; markdown is the brain, engine is just its index.
- **Depends on.** S1.
- **Recommended answer.** **Two-tier (1), but with a hard "wiki must justify itself"
  test:** adopt the wiki layer only for content classes the engine serves badly —
  *narrative/architectural knowledge, onboarding, cross-agent shared canon, and
  anything a human needs to read/edit directly.* Everything episodic stays
  engine-only. This keeps the wiki small and high-value instead of a second copy
  of everything.
- **Grill focus.** "Name three facts that *must* live in the wiki and could not be
  a Hindsight observation. If you can't, S2 should be Option 2."

---

## A. Engine

### A1. Hindsight, pgvector-minimal, or keep Cognee — as the recommended target?
- **Why it matters.** Picks the thing we build a provider for.
- **Options.** Hindsight · pgvector-minimal · Cognee-hardened.
- **Depends on.** S1.
- **Recommended answer.** **Hindsight as the target hypothesis**, validated by A3.
  Reasons (doc 01/05): TEMPR retrieval + token-budgeted recall, observations =
  built-in consolidation/staleness, bank-templates map cleanly onto hal0's
  TOML-template idiom, and *upstream Hermes + Claude-Code plugins already exist*.
- **Grill focus.** "Hindsight's bundled stack assumes its own BGE + reranker +
  Gemma. You want to rip all three out and route to hal0 slots + Lemonade. How
  much of Hindsight's quality story survives that surgery — is it still Hindsight
  or just their Postgres schema?"

### A2. Structured extraction: invest in it, or design to never need it?
- **Why it matters.** The single fact that explains why Cognee's graph is off:
  reliable triple/entity extraction needs ≥32B grammar-constrained models hal0
  can't reliably run locally (doc 05). This decision sets a *ceiling* on every
  graph/knowledge-graph feature, in either engine.
- **Options.**
  1. **Design to not need it** — semantic + BM25 + temporal + rerank only; graph
     stays a "someday, when local models are good enough" feature.
  2. **Invest** — stand up a 32B+ grammar-constrained extractor (where? Strix can
     hold it but it competes with the primary slot for memory).
  3. **Route extraction off-box** — but hal0 is no-cloud, so this is a non-starter
     unless to another LAN host.
- **Depends on.** A1.
- **Recommended answer.** **Option 1 now, architected so Option 2 is a later
  toggle.** The MemoryProvider's `retain()`/`consolidate()` should treat
  extraction quality as a pluggable knob, not a hard dependency. Don't block the
  redesign on a capability local models don't reliably have yet.
- **Grill focus.** "If extraction is off, Hindsight's 'biomimetic graph' is just a
  vector store with freshness metadata. Did we just pick Hindsight for features
  we've decided not to turn on?"

### A3. The eval — what exactly gates the cutover, and what's the pass bar?
- **Why it matters.** Doc 06 §4 and doc 07 P1 both make this a **hard gate: no
  flip blind.** But "run an eval" is not a decision until we define pass/fail.
- **Options for the bar.**
  1. **Quality-primary** — Hindsight must beat Cognee-today on a recall-relevance
     set by margin X.
  2. **Parity-is-enough** — match Cognee quality but win on staleness/legibility/ops.
  3. **Latency-bounded** — must stay under N ms p95 recall on Strix CPU embed.
- **Depends on.** A1, A2, and **A4 (Strix accel)**.
- **Recommended answer.** **Parity-or-better on relevance (2) AND a latency ceiling
  (3),** measured with the δ-harness on the *actual* primary Strix model and a
  representative hal0 memory corpus. Hindsight wins the tie on staleness + the
  existing plugins. Cognee-vs-Hindsight-vs-pgvector all three in the bake-off so
  Option 2 (S1) stays a live fallback.
- **Grill focus.** "We have no labelled hal0 recall set today. Are we going to
  hand-build eval data, or is this 'gate' actually just vibes-on-a-demo?"

### A4. Strix-Halo acceleration: validate, or assume CPU?
- **Why it matters.** Hindsight's docs say *nothing* about ROCm/XDNA (doc 01).
  Embeddings + rerank on CPU is fine; but the assumption needs a number before A3.
- **Options.** Assume CPU and move · Spend a day validating iGPU/NPU embed paths first.
- **Depends on.** —
- **Recommended answer.** **Assume CPU for embed/rerank** (route through existing
  embed/:8086 slots which already decide their own backend), **route the extraction
  LLM to `lemond:13305`** (iGPU), and treat any iGPU embedding win as later upside.
  Don't let an unvalidated accel assumption sit *inside* the eval.
- **Grill focus.** "The embed slot's backend choice is hal0's, not Hindsight's — so
  'CPU embed' might already be false. Did you confirm what the embed slot actually
  runs on before baking 'assume CPU' into the plan?"

---

## B. Namespace & multi-agent

### B1. Confirm the namespace scheme and bank mapping.
- **Why it matters.** Determines isolation, sharing, and how banks map.
- **Options.** `shared` / `private:<agent>` / `project:<id>` (+ `agents` identity
  set) with **1:1 namespace↔Hindsight-bank** · vs a flatter or richer scheme.
- **Depends on.** A1.
- **Recommended answer.** **Keep the existing three-namespace primitive 1:1 with
  banks.** It already exists in `namespace.py`, the #317 fix made it real, and it
  maps onto Hindsight banks without translation.
- **Grill focus.** "1:1 bank-per-namespace — does Hindsight charge per-bank
  overhead (separate index/worker)? `project:<id>` could spawn hundreds. What's the
  bank lifecycle/GC story?"

### B2. External agents (Claude Code, Cursor) — default read/write policy.
- **Why it matters.** hal0's MCP host exposes memory tools to *any* connecting
  agent. The default trust posture is a security/coherence decision.
- **Options.**
  1. **Guest** — read `shared` + own `private:`, **no `shared` write** by default.
  2. **Trusted** — full read/write like Hermes.
  3. **Sandboxed** — only own `private:`, no shared read.
- **Depends on.** B1, and D (promotion gate).
- **Recommended answer.** **Guest (1).** External agents read the curated canon and
  their own scratch, but writing to `shared` goes through the same promotion gate
  as everyone else (branch D). This is the "deeply integrated but not a free-for-all"
  posture the platform wants.
- **Grill focus.** "A user's Claude Code that *can't* persist a shared insight will
  feel second-class — the opposite of 'native'. Is guest-read-only the integration
  the user asked for, or a hedge?"

---

## D. Promotion model — **the crux**

### D1. Promotion direction between engine and wiki.
- **Why it matters.** This is the seam doc 05/06 both call the weakest point. Get
  it wrong and you get drift (two canonical copies) or a dead wiki (nothing flows).
- **Options.**
  1. **One-way, engine→wiki, gated** — engine accrues observations; curated ones
     get promoted *up* into the wiki; wiki→engine is only a derived read-index.
  2. **Wiki-canonical** — humans/agents write the wiki; engine is purely its index
     (Karpathy-pure).
  3. **Bidirectional** — both can be source-of-truth per fact-class.
- **Depends on.** S2.
- **Recommended answer.** **One-way gated (1).** The wiki is canonical *for what's
  in it*; the engine is canonical for raw recall; promotion flows up under a gate;
  the wiki is embedded into the engine **one-way** as "mental-model" priors checked
  first, never read back as truth. This gives exactly one source of truth per fact.
- **Grill focus.** "One-way means a human editing the wiki can't correct the
  engine — the engine will keep surfacing the stale observation the human just
  fixed. Doesn't that violate 'single source of truth' the moment a human edits?"

### D2. The gate mechanism for `shared`/`project:` promotion.
- **Why it matters.** Architect's #1 risk: the gate becomes a bottleneck or a
  rubber-stamp, and the curated layer stays empty — the failure mode that kills
  two-tier systems.
- **Options.**
  1. **Human-approval inbox** (reuse the MCP approval-queue UI).
  2. **Librarian-agent auto-promote** with periodic human audit.
  3. **Proof-count threshold** — Hindsight `proof_count`/corroboration auto-promotes.
  4. **Hybrid** — librarian drafts → human one-click approve.
- **Depends on.** D1, C3 (who the librarian is).
- **Recommended answer.** **Hybrid (4):** librarian agent drafts wiki entries from
  high-corroboration observations, queued to a lightweight approval inbox; one-click
  approve writes + re-embeds. Tune toward auto (2/3) once trust is established.
- **Grill focus.** "Every two-tier knowledge system dies on the approval queue.
  What's the *concrete* daily volume, and who is the human that clears it — because
  if it's nobody, the honest design is auto-promote (2/3) from day one."

### D3. Does consolidation **mutate** the shared brain, or only re-rank?
- **Why it matters.** (landscape Q2) If `reflect()`/consolidation can rewrite or
  retire shared observations autonomously, that's an agent mutating canon without a
  gate — in tension with D1/D2.
- **Options.** Mutate (autonomous belief revision) · Rank-only (never destroys,
  only re-weights) · Mutate-private-only.
- **Depends on.** D1, D2.
- **Recommended answer.** **Rank-only for `shared`; mutate freely within
  `private:<agent>`.** Belief revision is fine in an agent's own head; revising
  shared canon is a promotion-gated event, not a background job.
- **Grill focus.** "Hindsight's whole pitch is autonomous consolidation. If you
  forbid it on `shared`, are you fighting the engine's design — and does
  rank-only even fix staleness, or just hide it?"

### D4. Per-namespace promotion policy.
- **Recommended answer.** `private:<agent>` = auto-promote/auto-mutate (cheap,
  attributed, the agent's own scratch); `shared`/`project:` = gated per D2.
  *Mostly a corollary of D1–D3; included so the grilling can confirm it explicitly.*

---

## C. Wiki layer

### C1. Adopt the wiki now, or defer to a later milestone?
- **Depends on.** S2 (if engine-only wins, C is dead).
- **Recommended answer.** **Adopt, but after the engine cutover (roadmap P3),** so
  we're not landing two big changes at once.
- **Grill focus.** "Deferring the wiki means Phases 1–2 ship a pure engine swap with
  *no* user-visible win. Is there a thin wiki slice worth pulling forward to make
  the early phases feel like progress?"

### C2. Where the vault lives and who renders it for humans.
- **Why it matters.** hal0 runs headless on CT105; Obsidian is a desktop GUI.
- **Options.** Dashboard-rendered (graph.html / built-in viewer) · git→desktop
  Obsidian sync · both.
- **Recommended answer.** **Vault at `/var/lib/hal0/wiki`, git-backed; dashboard
  render is primary** (lives where the rest of hal0's UI is), **git→Obsidian sync is
  the power-user secondary.** The Agent→Memory dashboard tab becomes the wiki browser.
- **Grill focus.** "Building a Markdown+wikilink+graph renderer in the dashboard is
  real frontend work. Is rebuilding Obsidian-lite in-house justified versus just
  shipping the git repo and letting users point their own Obsidian at it?"

### C3. Who maintains the wiki — Hermes-as-librarian or a dedicated librarian agent?
- **Why it matters.** (architect Q3) Centralizing maintenance in one agent is
  simpler but a single point of failure / bottleneck.
- **Options.** Hermes wears the librarian hat · a dedicated lightweight librarian
  persona · any-agent-can-edit-with-lint.
- **Depends on.** D2.
- **Recommended answer.** **Dedicated librarian persona** (a Hermes persona/profile,
  not a separate runtime) that owns the nightly maintenance timer, lint, and
  promotion drafting. Keeps maintenance accountable to one identity without a new
  daemon.
- **Grill focus.** "A 'librarian persona' still runs on the one Hermes instance —
  same bottleneck, fancier name. What happens to wiki freshness when Hermes is busy
  serving chat?"

### C4. Fork strategy for hal0-wiki.
- **Why it matters.** The fork is byte-identical to upstream `ar9av/obsidian-wiki`
  today (doc 02) — a blank canvas. We can track upstream or harden a hal0 package.
- **Options.** Track upstream (symlink skills, `pip -U`) · Hard-fork into a hal0 pkg
  · Vendor the skills into hal0's own skills tree.
- **Recommended answer.** **Track upstream initially** (skills are markdown; cheap to
  re-pull), **vendor only the one change we know we need: swap the QMD search index
  for the engine** via the wiki's pluggable index seam. Hard-fork only if upstream
  diverges from our needs.
- **Grill focus.** "Tracking upstream means an `ar9av` skill update could silently
  change how hal0's brain is maintained. For a *core* platform subsystem, is
  uncontrolled upstream drift acceptable?"

---

## E. Integration mechanics

### E1. The `MemoryProvider` ABC shape.
- **Recommended answer.** Confirm doc 06's sketch: byte-compatible core
  (`add/search/list_items/delete/graph_status/set_graph/set_rerank`) so existing
  callers don't change, plus optional `recall`/`reflect`/`consolidate`/
  `register_compiled` (wiki→engine one-way). `MemoryItem.id` becomes the real join
  key, killing the SQLite text-join. Conformance suite gates any provider.
- **Grill focus.** "Byte-compatible core preserves the *current* contract — which
  leaks Cognee concepts (dataset enums, graph-route flags). Are you enshrining
  Cognee's abstractions in the ABC that's supposed to outlive Cognee?"

### E2. Who owns the working-memory / recall token budget?
- **Question (architect Q6).** Recall returns a token-budgeted set — does the
  *provider*, the *consumer* (Hermes prefetch), or a *central policy* set the budget?
- **Recommended answer.** **Consumer sets the budget, provider enforces it.** Hermes
  knows its context window and persona; the provider just honors a `max_tokens`.
- **Grill focus.** "If every consumer picks its own budget, shared recall quality is
  inconsistent across agents. Should there be a platform default they override,
  rather than free-for-all?"

### E3. The `:9077` daemon collision (architect Q8).
- **Question.** Hindsight's Hermes plugin runs a local daemon on `:9077`; does that
  collide with anything in hal0's port map (lemond 13305, admin 9000, slots 8001+,
  rerank 8086, serena 9121/9122)?
- **Recommended answer.** **Verify the port map and pin Hindsight's port explicitly**
  in config; don't rely on its default. Low-risk but must be checked before P1.
- **Grill focus.** "Do we even want Hindsight's *own* daemon, or should hal0 embed
  the engine in-process behind the provider and skip the extra service entirely?"

### E4. Webhook per-bank registration (architect Q9 / landscape).
- **Question.** The local skill only documents env-level webhook config
  (`WEBHOOK_URL`/`_SECRET`/`_EVENT_TYPES`) for `consolidation.completed` and
  `retain.completed`. A *per-bank* registration API was not confirmed.
- **Recommended answer.** **Verify against the live API/openapi before relying on
  per-bank webhooks**; design the promotion pipeline (P4) to work with the
  env-level firehose + an internal router as the fallback, so we're not blocked on
  an unconfirmed feature.
- **Grill focus.** "The promotion pipeline's trigger is `consolidation.completed`.
  If per-bank webhooks don't exist, every bank's consolidation hits one global
  endpoint — does the router scale, and can it even tell which namespace fired?"

### E5. Dashboard "Memory map" name collision.
- **Question.** `memory-map.jsx` is hardware GTT/RAM; the real brain explorer is the
  Agent→Memory tab. Two different "memory" surfaces confuse users.
- **Recommended answer.** **Rename the hardware view** (e.g. "Memory & GTT" or
  "Hardware Memory") and reserve "Memory"/"Brain" for the knowledge surface. Cheap,
  do it during P5.
- **Grill focus.** "Is 'Brain' the user-facing name we want, or marketing fluff?
  Pick the vocabulary now so docs/UI/skills don't drift."

### E6. Cognee→Hindsight migration fidelity (architect Q10).
- **Question.** Existing Cognee data = LanceDB vectors + the SQLite sidecar
  (filters/tags/dates are *only* in the sidecar). How faithfully must it migrate?
- **Options.** Full re-ingest (re-embed raw text into Hindsight) · metadata-only
  bridge · accept lossy / start-fresh with an archive.
- **Recommended answer.** **Re-ingest from the sidecar's authoritative text+metadata**
  (the sidecar *is* the real source of truth per doc 03), re-embedding into
  Hindsight; keep a read-only Cognee archive for one release as rollback.
- **Grill focus.** "Re-embedding changes vectors — recall results *will* shift.
  How do you prove the migration didn't silently lose or reorder knowledge users
  rely on?"

---

## F. Process & sequencing

### F1. ADRs.
- **Recommended answer.** New ADRs that **supersede ADR-0005/0014** (the Cognee/graph
  decisions). At minimum: ADR "memory engine = Hindsight behind MemoryProvider",
  ADR "two-tier brain + promotion model", ADR "wiki layer adoption". ADRs live in
  `docs/internal/adr/` (note: `docs/adr/` is empty — don't put them there).
- **Grill focus.** "Which of these is reversible enough to *not* need an ADR yet?
  Don't ADR-lock decisions the eval (A3) might overturn."

### F2. Milestone & promo sync.
- **Recommended answer.** Target **v0.4** (current default per auto-memory). After
  any phase that changes installer/capabilities/agents, sync README + PLAN +
  hal0-web CONTENT_BRIEF/Astro pages (standing feedback rule).
- **Grill focus.** "This is a multi-phase epic spanning a Postgres daemon, an engine
  swap, a wiki subsystem, and dashboard work. Is that one v0.4, or are we
  over-stuffing a milestone?"

---

## Quick map: open question → source → roadmap blocker

| ID | Question | Source | Blocks roadmap phase |
|----|----------|--------|----------------------|
| S1 | Replace vs harden engine | 03,05 | P0/P1 framing |
| S2 | Two-tier vs single-tier | 05,06 | P3,P4 (whole wiki arc) |
| A1 | Hindsight vs pgvector vs Cognee | 01,05 | P1 |
| A2 | Invest in extraction vs not | 05 | P1 (feature ceiling) |
| A3 | Eval pass bar | 06,07 | **P2 hard gate** |
| A4 | Strix accel validate vs assume | 01 | P1 (eval input) |
| B1 | Namespace/bank mapping | 04,06 | P2 |
| B2 | External-agent default policy | 04,05 | P5 |
| D1 | Promotion direction | 05,06 | P4 |
| D2 | Gate mechanism | 06 | **P4** |
| D3 | Consolidation mutate vs rank | 05 | P4 |
| C2 | Vault location/render | 02,06 | P3 |
| C3 | Librarian centralization | 06 | P3,P4 |
| C4 | Fork strategy | 02 | P3 |
| E2 | Token-budget owner | 06 | P5 |
| E3 | :9077 collision | 06 | P1 |
| E4 | Per-bank webhooks | 01,06 | P4 |
| E6 | Migration fidelity | 03,06 | P2 |
| F2 | YAGNI / milestone scope | 06 | P6 |

---

## Suggested grilling order (one pass)

1. **S1, S2** — settle scope. If S2 → engine-only, skip branches C and most of D.
2. **A2, then A1, A4 → A3** — the engine + the eval gate (the technical spine).
3. **B1, B2** — isolation/trust.
4. **D1 → D2 → D3 → D4** — the promotion crux (do this with full attention).
5. **C1–C4** — wiki specifics (only as deep as S2/D allow).
6. **E1–E6** — wiring details.
7. **F1, F2** — ADRs + milestone scoping.

Resolve each node, record the decision inline, and convert the settled tree into
ADRs (F1) and issues (`/to-issues`) afterward.
