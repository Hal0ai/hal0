# 07 — Integration Roadmap: hal0 Unified Brain

**Status:** Sequenced build plan. Derived from doc 06 (authoritative architecture), doc 03 (as-built audit), doc 04 (consumer surface), doc 01/02/05 (engine/wiki/landscape specifics).
**Date:** 2026-06-02
**Author:** Technical Planner
**Reads against:** ADR-0005/0014 (superseded by 06), ADR-0011/0012 (extended). Open design questions tracked in doc 08; this roadmap flags every dependency on them with **[Q#]**.

> **How to read this.** Each phase is a *vertical tracer-bullet slice* that leaves
> `main` shippable. For each phase: **Goal**, **Work items** (file-level where the
> audit gives paths), **Exit criteria** (the verification gate), **Rollback**,
> **"Can ship after this phase"**, and **Risk / blocked-on**. Hard gates are called
> out in bold. The single most important rule of the whole arc, from doc 06 §4:
> **do not flip the engine blind — the δ-eval (Phase 1) is a hard gate before the
> Phase 2 cutover.**

---

## Dependency graph

```
 Phase 0  Seam & safety net  (MemoryProvider ABC + conformance suite + _client.py 404 fix)
    │   ← prerequisite to everything (doc 06 §3 "the linchpin")
    ▼
 Phase 1  Hindsight behind the seam (SHADOW)  + δ-eval  ──────────┐
    │   needs: Phase 0 ABC                                         │
    │   ┌──────────────────────────────────────────────┐          │
    │   │  ★ HARD GATE: δ-eval Cognee vs Hindsight vs   │          │
    │   │    pgvector on real Strix model [Q2]          │          │
    │   └──────────────────────────────────────────────┘          │
    ▼                                                              │
 Phase 2  Cutover + namespace/bank mapping                        │
    │   needs: Phase 1 + δ-eval PASS; Cognee kept as fallback flag │
    ▼                                                              │
 Phase 3  Wiki layer (vault + skills + register_compiled index)   │
    │   needs: Phase 2 (engine is the wiki search index)           │
    ▼                                                              │
 Phase 4  Promotion pipeline (webhook → EventBus → gated queue)   │
    │   needs: Phase 1 (webhooks) + Phase 3 (wiki write target) [Q1][Q9]
    ▼
 Phase 5  Deep consumer integration (Hermes recall/retain, CC plugin,
    │      dashboard Memory tab, name-collision fix, bank-templates)
    │   needs: Phase 2 (engine), Phase 3 (wiki), Phase 4 (inbox) — partial
    ▼   [Q3][Q5][Q7][Q8]
 Phase 6  Polish (per-bank webhooks, observability, docs/promo, ADRs)
        needs: all prior  [Q1..Q11 closure]
```

Parallelizable: Phase 3 (wiki skills) can begin in a worktree during Phase 1's
eval wait, since it only depends on the `register_compiled` ABC method (Phase 0),
not on the flip. The Hermes `_client.py` 404 fix (Phase 0) and the dashboard
name-collision rename (Phase 5) are independent and can land any time.

---

## Phase 0 — Seam & safety net

**Goal.** Promote the implicit five-method `CogneeWrapper` contract into an
explicit `MemoryProvider` ABC + Protocol with a parametrized conformance suite,
and route the one construction site through a config factory — **with zero
behaviour change**. Cognee remains the only live engine. Also fix the latent
Hermes `_client.py` 404 route mismatch found in the audit. This is the
prerequisite to every later phase (doc 06 §3).

**Work items.**
- **New `src/hal0/memory/provider.py`** — exactly the ABC/Protocol and value
  types in doc 06 §3 (`MemoryItem`, `AddResult`, `ListPage`, `DeleteResult`,
  `GraphStatus`, `Mode`; abstract core five `add/search/list_items/delete` +
  runtime-flip `graph_status/set_graph_enabled/set_rerank_enabled`; optional
  `recall/reflect/consolidate/register_compiled` with safe defaults). The core
  five signatures must be **byte-compatible** with `CogneeWrapper` (audit §5
  enumerates them) so no call site changes.
- **`src/hal0/memory/cognee_wrapper.py`** — make `CogneeWrapper(MemoryProvider)`
  subclass the new ABC. No logic change; just declare conformance. Add the
  default `recall` (clip `search`), `reflect` (raise `NotImplementedError`),
  `consolidate` (no-op), `register_compiled` (`add(..., tags=[*tags,'wiki'])`).
- **New `provider_from_config(cfg)` factory** in `src/hal0/memory/__init__.py`
  (re-export per the package's "only the wrapper is public" rule, audit §5).
  Phase 0 returns `CogneeWrapper(...)` always; later phases add the branch.
- **`src/hal0/api/__init__.py:1108`** — replace `CogneeWrapper(...)` with
  `provider_from_config(cfg)`. State key `app.state.memory_wrapper` (`:1121`) and
  the dispatcher hand-off (`:1136`) unchanged. Rename the state key to
  `app.state.memory_provider` is **deferred to Phase 2** to avoid a wide diff now.
- **New `src/hal0/memory/pgvector_provider.py` (stub)** — a minimal
  `PgVectorProvider(MemoryProvider)` that satisfies the ABC (core five over a
  pgvector table; `reflect` raises). Needed as the conformance-suite third
  implementation and the Phase 2 fallback. May be skeletal here; fleshed in
  Phase 1.
- **New `tests/memory/test_provider_contract.py`** — the single parametrized
  conformance suite from doc 06 §3, run against `CogneeWrapper`, `PgVectorProvider`,
  and the existing `_FakeWrapper`/`StubWrapper` test doubles (audit §4.4). Assert:
  namespace isolation, `private:` write rejection, tag-AND, date-range filter,
  delete semantics, **fail-open-empty foreign-private read** (audit §2.1), and
  the `graph_status()` payload-shape contract (every key the dashboard reads,
  audit §5).
- **Fix `src/hal0/agents/hermes/plugins/memory_cognee/_client.py`** — the latent
  404 (audit §4.2): `list_items` hits `GET /api/memory` (no such route → use
  `GET /api/memory/list`); `delete` hits `DELETE /api/memory/{id}` (no such route
  → use `POST /api/memory/delete` with `{ids}` body). Add a route-path test
  asserting the client paths against the real router so this can't silently
  re-drift. (If the methods are confirmed dead, delete them instead — but the
  test is the point.)
- **CI:** wire `test_provider_contract.py` into the **default** gate (not the
  `slow`/Cognee-fixture exclusion that hid §4.2/§4.4). Run `ruff format --check`
  too (per the standing CI gotcha).

**Exit criteria.**
- Conformance suite green for `CogneeWrapper` + `PgVectorProvider` + fakes.
- `provider_from_config` returns `CogneeWrapper`; full app boots; a manual
  `add → search → list → delete` round-trip via `/api/memory/*` and `/mcp/memory`
  is byte-identical to pre-change behaviour.
- Hermes `_client.py` route-path test green against the real router.
- No diff to MCP server, REST shims, dispatcher, CLI, or the Hermes provider call
  sites (only the construction site + the client fix).

**Rollback.** Pure additive + one-line construction swap. Revert the
`api/__init__.py:1108` line to `CogneeWrapper(...)`; the ABC and tests are inert.
Zero data implications.

**Can ship after this phase.** Yes — identical runtime behaviour, better-tested,
plus a real latent-404 bug fixed. Strict improvement.

**Risk / blocked-on.** Low. **No open-question dependency.** Only subtlety:
getting the ABC signatures byte-compatible so later phases don't have to touch
call sites — the audit §5 enumeration is the spec; verify against actual
`cognee_wrapper.py` method headers, not the doc.

---

## Phase 1 — Hindsight behind the seam (shadow) + the δ-eval gate

**Goal.** Stand up Hindsight on CT 105 as a systemd unit, implement
`HindsightProvider` against the Phase-0 ABC, wire its embeddings/rerank/extraction
to existing hal0 slots, and **run the δ-eval** (Cognee-as-used vs Hindsight-recall
vs plain-pgvector) on the *actual* Strix-Halo primary model and a representative
corpus. Hindsight runs in **shadow** — built and conformance-tested, but
`provider_from_config` still defaults to Cognee. **No flip in this phase.**

**Work items.**
- **Deploy Hindsight on CT 105** as a systemd unit beside lemond
  (`hindsight-api`, full image, API `:8888`, control-plane `:9999` optional).
  Data root `/var/lib/hal0/memory/hindsight/`; **embedded pg0** bound to
  `/var/lib/hal0/memory/hindsight/pg0` (NOT a separate Postgres daemon —
  doc 06 §4, doc 01 §8.4). `HINDSIGHT_API_RUN_MIGRATIONS_ON_STARTUP` handled per
  doc 01 §5. Build on the hal0-dev VM if a docker build is needed (LXC apparmor
  build denial is a known constraint), then `docker save | ssh hal0 docker load`.
- **Wire to slots** (doc 06 §4 table, doc 04 §4):
  - Embeddings → **embed capability slot** via Hindsight's OpenAI-compatible/TEI
    embedding provider (`HINDSIGHT_API_EMBEDDING_*`). **Do not bundle BGE.** If the
    embed slot's model ≠ Cognee's `bge-small-en-v1.5` (384-dim), a **re-embed on
    cutover** is required — record the slot's actual model now. **[Q4]** (Strix
    accel undocumented; assume CPU, validate empirically.)
  - Rerank → **rerank slot `:8086`** (`bge-reranker-v2-m3`) via Hindsight's
    external/TEI reranker provider — the same slot `cognee_wrapper.py:209` already
    POSTs to. Fallback: Hindsight's local MiniLM cross-encoder (CPU) or pure RRF.
  - Extraction LLM → **`HINDSIGHT_API_LLM_PROVIDER=openai` →
    `HINDSIGHT_API_LLM_BASE_URL=http://127.0.0.1:13305`** (lemond gateway). Mind
    `RETAIN_MAX_COMPLETION_TOKENS` vs `RETAIN_CHUNK_SIZE` (3000) per doc 01 §8.2.
- **New `src/hal0/memory/hindsight_provider.py`** — `HindsightProvider(MemoryProvider)`
  calling Hindsight over its REST/SDK in-process. Implements core five + overrides
  `recall` (true TEMPR token-budgeting), `reflect`, `consolidate`,
  `register_compiled` (creates a `kind='wiki'` mental_model). **Engine owns its own
  filtering** — map hal0 `tags` to Hindsight `tag:true` entity-label filter,
  date-range and dataset isolation to Hindsight banks (doc 06 §4, audit §6
  directive: retire the sidecar). Bank mapping per the Phase-2 table but
  *implemented here* so the eval uses the real mapping.
- **Flesh `PgVectorProvider`** into a runnable engine (the eval's third arm and the
  Phase-2 boot fallback).
- **`provider_from_config`** gains `engine = "cognee" | "hindsight" | "pgvector"`
  config (default **cognee**) + a degrade ladder (doc 06 §4): Hindsight unavailable
  at boot → fall back to pgvector/no-op; tools return `available:false`; dashboard
  renders "no engine" (doc 04 §8 — surface is already conditionally mounted).
- **Confirm the Hermes upstream-plugin port question [Q8 doc06 / Gap re :9077]:**
  verify Hermes' upstream Hindsight plugin `local` mode can be pointed at the
  standalone CT-105 API URL **without** auto-spawning its own `:9077` daemon. (We
  do *not* wire Hermes to Hindsight in this phase — just confirm the override
  exists, for Phase 5.)
- **★ The δ-eval harness** — extend hal0's δ/eval harness (`tests/harness/`) with a
  recall-quality eval: a representative hal0 memory corpus + a query set with
  graded relevance, run identically through `CogneeWrapper`, `HindsightProvider`,
  and `PgVectorProvider`, **on the actual Strix-Halo primary model** for extraction.
  Emit recall@k / nDCG / latency / ingest-cost JSON rows. (See the `llm-evaluation`
  skill for metric scaffolding.)

**Exit criteria — this phase's gate is two-part.**
1. **Conformance:** `HindsightProvider` and the runnable `PgVectorProvider` both
   pass `test_provider_contract.py` unmodified. Hindsight boots as a systemd unit;
   pg0 persists across restart; embed/rerank/extraction routed to the right
   slots/gateway and health-checked.
2. **★ HARD GATE — δ-eval result reviewed before Phase 2.** The
   "Hindsight recalls better than Cognee-as-used" claim is **architectural, not yet
   benchmarked** (doc 06 §10 Q2, doc 05 §4 honesty flag). The eval must run and be
   reviewed. Decision rule, explicit per doc 06 §10 Q2 / §11:
   - **Delta positive** → proceed to Phase 2 cutover.
   - **Delta null/negative** → escalate the open question: do we still pay the
     Postgres/Hindsight migration cost for the *consolidation/observations upgrade
     path* alone, or stay on Cognee (or drop to pgvector, doc 05 option d)? **This
     decision must be made by a human before any flip.**

**Rollback.** Trivial — nothing is flipped. Stop/disable the `hindsight-api`
systemd unit; `provider_from_config` is still defaulting to Cognee. Hindsight data
root is isolated under `/var/lib/hal0/memory/hindsight/`; deleting it touches no
Cognee data.

**Can ship after this phase.** Yes — Cognee is still the live engine; Hindsight is
dark. Shipping carries an idle systemd unit (resource cost only), or ship with the
unit masked.

**Risk / blocked-on.**
- **★ Blocked-on [Q2]:** the cutover (Phase 2) cannot proceed until the eval is run
  and reviewed. This is the spine of the whole plan.
- **[Q4]** Strix accel unknown — if CPU embed is too slow at ingest volume, may need
  bge-on-iGPU (embed slot), which could change the embedding model and force a
  re-embed. Surfaces here as a perf measurement in the eval.
- **[Q5]** Extraction quality tracks local-LLM quality; design for recall-only +
  degrade ladder so a weak extractor doesn't block the eval.
- **[Q8]** Upstream Hindsight plugin daemon-port collision — confirm override before
  relying on it in Phase 5.

---

## Phase 2 — Cutover + namespace/bank mapping

**Goal.** With the δ-eval cleared, flip `provider_from_config` to Hindsight as the
default engine, map hal0 namespaces to Hindsight banks, migrate existing Cognee +
sidecar data into banks with a verified fidelity plan, and keep Cognee behind a
fallback flag for one release.

**Work items.**
- **Bank ↔ namespace mapping** (doc 06 §4), implemented + tested in
  `HindsightProvider` and asserted in the conformance suite:
  | hal0 namespace | Hindsight bank |
  |---|---|
  | `shared` | `shared` |
  | `private:<agent>` | `private__<agent>` (`:`→`__`, bank-id-safe) |
  | `project:<id>` | `project__<id>` (auto-create on first use — formalises today's pass-through custom datasets, doc 05 §5) |
  | `agents` | `agents` (identity cards, ADR-0011) |
  The shared `hal0.memory.namespace` resolver (the #317 fix) is **unchanged**; it
  now maps to banks.
- **Migration** (doc 06 §10 Q10 — this is a flagged open question on *method*):
  move sidecar `hal0_memory_index.sqlite` rows (the real schema: `dataset / tags /
  source / metadata / timestamp`, audit §2.2) + Cognee chunks into the matching
  banks, preserving schema. **[Q10 — decide:]** dual-write window vs one-shot
  backfill+verify. The text-equality join (audit §4.3) means some Cognee rows may
  not cleanly map — **define and record the acceptable-loss threshold before
  running.** Build a `hal0 memory migrate` one-shot with a `--dry-run` that reports
  row counts mapped/unmapped and a post-migration **verify** pass (count + spot
  recall parity).
- **Flip the construction site:** `provider_from_config` default → `hindsight`.
  Rename `app.state.memory_wrapper` → `app.state.memory_provider` across the ~handful
  of readers (audit §1.1 stash sites) now that the engine is generic.
- **Fallback flag:** `[memory] engine = "cognee"` in `hal0.toml` reverts to Cognee
  for one release. Cognee package + `cognee_wrapper.py` stay in-tree (deletion is
  Phase 6 / doc 06 §9).
- **Retire the sidecar** as the filtering source of truth (Hindsight owns filtering
  now). Keep the sidecar file read-only until migration verify passes, then it's
  dead.

**Exit criteria.**
- δ-eval **PASS** on record (Phase 1 gate) — re-stated as the precondition.
- `hal0 memory migrate --dry-run` shows mapped/unmapped within the agreed loss
  threshold; live migration + verify pass (count + recall-parity spot check).
- Default boot uses Hindsight; `add/search/list/delete` over both transports green;
  namespace isolation + `private:` rejection + foreign-private fail-open-empty all
  pass against the live Hindsight engine.
- `[memory] engine = "cognee"` cleanly reverts and still serves the migrated-from
  Cognee store (it was never mutated).
- Per-agent stats route (`memory_stats.py`) still answers (real counts wired in
  Phase 5; `available` must be honest now).

**Rollback.** Set `[memory] engine = "cognee"` and restart hal0-api — instant
revert to the untouched Cognee store. Because migration is *copy-into-Hindsight*
(Cognee data is read-only during it), rollback loses only memories written to
Hindsight *after* the flip; mitigate with a short dual-write window **[Q10]** if the
team chooses that path.

**Can ship after this phase.** Yes — this is the headline release. Hindsight is the
live brain; Cognee is a one-flag escape hatch. Wiki/promotion not yet present, but
the engine upgrade (consolidation/observations, token-budgeted recall) ships.

**Risk / blocked-on.**
- **Blocked-on [Q2]** (must be cleared in Phase 1) and **[Q10]** (migration method +
  acceptable loss — decide before running).
- Main risk: silent recall regression on real traffic that the eval corpus didn't
  cover. Mitigation: the fallback flag + keep Cognee data untouched for one release.

---

## Phase 3 — Wiki layer

**Goal.** Stand up the git-backed Obsidian-flavored vault, install the hal0-wiki
skills into the agents, and make the wiki's **search index point at the Engine**
(not QMD) via `register_compiled`, with a librarian maintenance timer. Can begin in
parallel during Phase 1's eval wait (depends only on the Phase-0 `register_compiled`
ABC method), but lands after Phase 2 so the index target is the live Hindsight
engine.

**Work items.**
- **Vault at `/var/lib/hal0/wiki`** on CT 105 (beside `registry/`, `lemonade/`,
  `memory/hindsight/`). Just a markdown directory (doc 02 §7.3): `index.md`
  (content catalog), `log.md` (append-only op log), `hot.md` (warm-start cache),
  `.manifest.json` (source→pages ledger), and `concepts/entities/skills/...` trees
  (doc 02). **Git-backed** (periodic commit; btrfs snapshot as belt-and-suspenders).
- **Fork rename:** `obsidian-wiki` → `hal0-wiki` in the fork's `pyproject.toml`
  (currently still upstream's — doc 02 §6/§7.5, doc 06 §9). Set
  `OBSIDIAN_VAULT_PATH` / `~/.obsidian-wiki/config` for the agent user to the vault.
- **The deliberate fork edit (QMD → Engine)** — doc 06 §5, doc 02 §7.5. In the
  `wiki-ingest` / `wiki-update` / lint skills, replace the hard-referenced `qmd`
  call sites with `/api/wiki`-mediated `register_compiled(page_id, text, dataset,
  tags)` calls (one-way: Wiki → Engine index, `kind='wiki'` mental_model). In
  `wiki-query`, do a **semantic pass against the Engine first** (`recall` with
  `types=['wiki','observation']`) then fall back to Grep/Glob over the vault
  (preserve upstream's graceful-degrade path). Do this now while the fork is
  byte-identical to upstream.
- **Skill install:** `obsidian-wiki setup` (renamed `hal0-wiki setup`) installs the
  two portable skills `wiki-update` (write) / `wiki-query` (read) plus the wiki
  toolset into `~/.hermes/skills/` at Hermes provision time; also into
  `~/.claude/skills` and `~/.pi/agent/skills` (doc 02 §1.3 target dirs) so Pi and
  external Claude Code can read/write the same vault.
- **New `/api/wiki/*` REST surface** (sibling to `/api/memory/*`, doc 06 §2(c)):
  page render/list + `graph.json`/`graph.html` (the framework's server-free graph
  view) + the `register_compiled` mediation. Mounted in `create_app`.
- **Librarian maintenance timer** (doc 06 §5): a CT 105 **systemd timer** (the
  framework's launchd plist → a timer) runs `daily-update` nightly — freshness
  pass, `index.md`/`hot.md` rebuild, lint. **Hermes-as-librarian** for now **[Q3]**.
- **`.manifest.json` attribution** wired to the `X-hal0-Agent` identity that stamps
  every Engine write — the basis for `memory-bridge` (doc 06 §5).
- **Retire** the pre-existing local `obsidian-vault` skill on hal0-dev (flat WSL
  path, no provenance — doc 06 §9) to avoid two competing conventions.

**Exit criteria.**
- `hal0-wiki setup` installs skills into Hermes/Claude/Pi skill dirs; `info` shows
  per-agent install status.
- A `wiki-ingest` of a sample page writes markdown to `/var/lib/hal0/wiki`, commits
  to git, and calls `register_compiled` → the page is recallable from the Engine via
  `recall(types=['wiki'])`.
- `wiki-query` returns the page via the Engine semantic pass, and still returns it
  with the Engine down (Grep fallback).
- `/api/wiki/list` + `graph.json` render; nightly `daily-update` timer fires and
  rebuilds `index.md`/`hot.md`.

**Rollback.** The wiki is additive — a directory + skills + a sibling REST surface.
Disable the librarian timer, uninstall the skills (`setup` is reversible / symlinks),
and unmount `/api/wiki/*`. The engine is unaffected; `kind='wiki'` index entries can
be left (inert) or filtered out. No memory-engine rollback needed.

**Can ship after this phase.** Yes — the wiki is a read/write knowledge layer that
degrades to plain markdown. Promotion (Phase 4) not yet automated; humans/agents
write the wiki by hand via skills. Still a coherent, shippable "engine + notebook".

**Risk / blocked-on.**
- **[Q3]** Hermes-as-librarian centralisation (single failure + trust point). Start
  with Hermes; the timer/skill design must not assume Hermes-only so a dedicated
  librarian persona can be split out later without rework.
- Fork-divergence discipline: the QMD→Engine edit is the one big "Hal0'd" change;
  keep it surgical and documented so upstream re-syncs stay tractable (doc 02 §6).

---

## Phase 4 — Promotion pipeline

**Goal.** Wire the **one-way, gated** Engine→Wiki promotion (doc 06 §6): Hindsight's
`consolidation.completed` webhook fans into the hal0 EventBus and enqueues promotion
candidates; the librarian drafts a wiki page; a human approves via a dashboard inbox;
approval writes the page and `register_compiled` re-embeds it. Consolidation writes
to the Engine **only** — it never auto-writes the Wiki.

**Work items.**
- **Webhook receiver:** point `HINDSIGHT_API_WEBHOOK_URL` at a new hal0 endpoint
  (e.g. `/api/memory/webhooks/hindsight`) that verifies the HMAC
  (`HINDSIGHT_API_WEBHOOK_SECRET`), **dedupes on `operation_id`** (at-least-once
  delivery, doc 01 §6), and fans `consolidation.completed` into
  `src/hal0/events/` EventBus (footer/journal live update) (doc 06 §7).
  **[Q9 — Gap 2:]** the shipped Hindsight webhook config is **server-wide env-var
  only** (no documented per-bank registration, doc 01 §6). So the handler must
  **demultiplex by `bank_id`** in one endpoint to route candidates to the right
  namespace. Verify whether per-bank exists before assuming; if not, single-handler
  demux is the design.
- **Promotion-candidate queue:** an observation qualifies when `proof_count ≥ N`
  **or** `freshness == 'stable'` (doc 06 §6). Gate by namespace:
  - `private:<agent>` → **auto-promote** (low blast radius).
  - `shared` / `project:<id>` → **gated**: librarian agent drafts the wiki page
    (`provenance: inferred`, `lifecycle: draft`); a **human approves** via the
    dashboard inbox (reusing the existing destructive-call approval-queue UX —
    `src/hal0/mcp/approval_queue.py`, doc 04 §3.1) to flip `lifecycle: reviewed`.
- **Approval → write:** on approve, `wiki-ingest` writes the page and
  `register_compiled` re-embeds it as `kind='wiki'` (top of the recall hierarchy:
  wiki → observations → facts). One-way; the Engine never reads it back as mutable
  truth.
- **Dashboard health signal:** an aging promotion inbox surfaces as a health metric
  (mitigation for the §6 main risk).

**Exit criteria.**
- A forced `consolidate()` → `consolidation.completed` webhook → EventBus event +
  candidate enqueued, deduped on `operation_id` (replayed webhook is idempotent).
- `private:<agent>` candidate auto-promotes to a draft wiki page + Engine re-embed.
- `shared` candidate appears in the dashboard promotion inbox; human approve writes
  the page, flips `lifecycle: reviewed`, and re-embeds.
- Round-trip recall shows the wiki page ranked above the raw observation it came from.

**Rollback.** Unset `HINDSIGHT_API_WEBHOOK_URL` (promotion stops; consolidation still
runs internally to the Engine, harming nothing). Disable auto-promote (set all
namespaces to gated, or disable the queue). Already-promoted wiki pages remain valid
hand-written pages. No data loss.

**Can ship after this phase.** Yes — the brain now compounds: consolidation feeds a
gated pipeline into the curated wiki. The remaining work (Phase 5) is depth/UX, not
correctness.

**Risk / blocked-on.**
- **★ [Q1] — the headline open question.** Is `private:<agent>`-auto + `shared`-gated
  the right split? The gate is either a bottleneck or a rubber stamp (doc 06 §6 main
  risk, §10 Q1). On a single-user box, proof-count corroboration may never accrue
  **[Q6]** — so `freshness=='stable'` likely carries more weight than `proof_count ≥
  N`. **Resolve the default trust gate before shipping auto-promotion to `shared`.**
- **[Q9]** Webhook per-bank registration may not exist → single-handler demux design
  must be confirmed.

---

## Phase 5 — Deep consumer integration

**Goal.** Make every consumer feel the brain natively (doc 06 §7, doc 04 §6): Hermes
recall/retain wired to the engine's `recall`/`retain`; external Claude Code as a
first-class guest with the six-hook ingestion plugin; the dashboard Agent→Memory tab
with real stats + wiki browser + promotion inbox; the "Memory map" name-collision
resolved; bank-templates as a persona/project primitive.

**Work items.**
- **Hermes — recall:** `Hal0CogneeProvider.prefetch(query)` (`provider.py:137`) calls
  **`recall(types=['wiki','observation','world'], max_tokens=…)`** instead of flat
  `search(limit=5)`; returns a wiki-block + observation-block, top-of-hierarchy
  first (doc 06 §7). **Rename the plugin `hal0-cognee` → `hal0-memory`** (dir,
  `name`, README; copied into `$HERMES_HOME/plugins/memory/hal0-memory/`).
- **Hermes — write:** `sync_turn` (`provider.py:163`) + `on_memory_write` (`:207`)
  unchanged call path → now backed by Hindsight `retain`; background consolidation
  actually runs (it didn't on Cognee). (The `_client.py` 404 was already fixed in
  Phase 0.)
- **Hermes — context files:** add a "Wiki index / how to query the brain" section to
  `HERMES.md.j2` / `AGENTS.md.j2` via `_phase_context_link`
  (`hermes_provision.py:1230`) so any agent landing in `/etc/hal0` learns the brain
  exists (doc 04 §6 #9).
- **External Claude Code:** add `wiki_search` / `wiki_get` tools on the **same**
  `hal0-memory` MCP server (not a new service, doc 06 §2). Default an external client
  to **read `shared` + own-private, no shared-write** **[Q7 — confirm read-yes is
  acceptable for a LAN guest]** (doc 06 §10 Q7). Ship the **six-lifecycle-hook
  ingestion plugin** (SessionStart / UserPromptSubmit / PostToolUse / Stop /
  PreCompact / SessionEnd) so Claude Code *produces* into the wiki — the headline
  "every agent feeds one brain" story (doc 04 §6 Tier C-8, PLAN:1044).
- **pi-coder:** Wiki tools appear automatically via MCP (`pi-mcp-adapter.json` →
  `/mcp/memory`). Mine the `pi-memory-md` precedent as the `project:<id>` wiki-
  namespace analogy (doc 04 §1.3). No code change required beyond MCP tool exposure.
- **Dashboard Agent→Memory tab** (`ui/src/dash/agents/memory-tab.jsx`): wire **real
  stats** (Hindsight `get_bank_stats` + `/stats` pending-consolidation — replaces the
  mock `2,847`); add an **Obsidian wiki browser** (render markdown +
  `graph.html`/`graph.json`); add the **promotion inbox** (reuse approval-queue UX).
  Keep `GraphExtractionPanel` as the engine graph-gate. **Wire real `reads`/`writes`
  counts** (audit §4.6 — `reads` is hard-coded `0`, `writes` is a capped page count).
- **Resolve the "Memory map" name collision:** `ui/src/dash/memory-map.jsx` is
  **hardware GTT/RAM attribution, NOT the brain** (doc 04 §5, audit §3). Rename the
  hardware view (e.g. "Hardware Memory" / "Unified Memory map") so it cannot be
  confused with the Agent→Memory brain tab. Mechanical but do it before docs/promo.
- **`memory` capability card** (optional, doc 06 §7 / doc 04 §7): add to
  `capabilities.toml` / `capabilities/catalog.py` so the brain's embed+rerank wiring
  + health render in the same UX as voice/img cards.
- **Bank-templates as a hal0 primitive** (doc 06 §8): JSON templates at
  `/etc/hal0/memory-templates/<id>.json` (echoing `/etc/hal0/mcp-servers/<id>.toml`).
  Seed set: `coding-agent`, `conversation`, `personal-assistant`, `homelab-ops`.
  Extend personas (`personas.py`) with a `memory_bank_template` field; on persona
  select, idempotently `import` the template to ensure the bank exists. `export`
  emits overrides → version-controlled artifact; `import --dry-run` in CI.
  **[Caveat — doc 01 Gap 3:]** confirm the importer accepts the template
  `entity_labels` form (flat `string[]`) vs bank-config rich label-group objects, and
  `retain_extraction_mode: chunks`, before shipping rich-label templates.
- **journal/events producer (optional, off by default, doc 06 §7):** a gated producer
  that ingests operational events into `shared` tagged `["machine","event"]`. Ship
  behind a flag; default off (volume/noise).

**Exit criteria.**
- Hermes prefetch returns wiki + observation blocks (hierarchy-ordered) on a live
  turn; `sync_turn` write triggers eventual consolidation.
- A real external Claude Code session reads `shared`, is blocked from shared-write,
  and the six-hook plugin produces a wiki page from a session.
- Dashboard Memory tab shows non-mock stats, renders a wiki page + graph, and the
  promotion inbox approves a candidate end-to-end.
- "Memory map" hardware view renamed; no UI string confuses it with the brain tab.
- Selecting a persona with a `memory_bank_template` provisions the bank
  idempotently; `import --dry-run` passes in CI.

**Rollback.** Per-surface and granular: the plugin rename is reversible; `prefetch`
can revert to flat `search`; the CC six-hook plugin is opt-in; the dashboard tab can
fall back to the engine-only explorer; bank-templates default to none; the events
producer is flagged off. None touch engine data.

**Can ship after this phase.** Yes — this is the "feels native" release. Every
consumer reaches the unified brain through the unchanged contract.

**Risk / blocked-on.**
- **[Q3]** librarian split (if Phase 4 surfaced contention).
- **[Q5]** extraction quality — Hermes recall quality tracks it.
- **[Q7]** external-agent default read (read-yes / shared-write-no) — confirm before
  exposing wiki tools to LAN guests.
- **[Q6]** proof-count on a single-user box informs the bank-template defaults
  (disposition/promotion thresholds).

---

## Phase 6 — Polish

**Goal.** Close the operational and documentation loop: per-bank webhooks,
observability/monitoring, docs + promo sync, ADRs, and delete the Cognee corpse.

**Work items.**
- **Per-bank webhooks** if Hindsight gains/exposes per-bank registration (else keep
  the Phase-4 single-handler demux) **[Q9 closure]**.
- **Observability/monitoring:** Hindsight `/stats` counters (pending consolidation,
  per-bank sizes) surfaced to the dashboard + footer; promotion-inbox-age health
  signal; audit-trail aggregation to finally back the `reads`/`writes` counts
  honestly (audit §2.4 / §4.6).
- **Delete Cognee** once the fallback flag has soaked one release without revert
  (doc 06 §9): remove `cognee==1.0.7`, `cognee_wrapper.py`, the sidecar
  `hal0_memory_index.sqlite` + text-equality join, and the write-only Kuzu graph.
  Drop the `engine = "cognee"` branch.
- **ADRs:** mark **ADR-0005 / ADR-0014 superseded**; write new ADRs for the
  `MemoryProvider` ABC + engine choice (Hindsight), the promotion model, and
  bank-templates-as-primitive. Mark the stale auto-memory entry
  `hal0_memory_dataset_namespace_bug` resolved (#317 fixed, audit §4.1).
- **Docs + promo sync** (per the standing reminder): README (bundled `hal0-memory`
  description, wiki, brain), PLAN (engine pivot, wiki, promotion), hal0-web
  CONTENT_BRIEF + Astro pages.

**Exit criteria.** Cognee fully removed and CI green without it; ADRs landed; README/
PLAN/hal0-web reflect the unified brain; observability panels live.

**Rollback.** Cognee deletion is the only irreversible step — do it **only after** the
fallback flag soaked one release with no revert. Everything else is additive/docs.

**Can ship after this phase.** Yes — terminal, fully-polished state.

**Risk / blocked-on.** **[Q11]** YAGNI counter-case (is the two-tier design over-built
for a single-user box?) — if the eval/usage data says the Wiki-in-context suffices,
Phase 6 is where you'd consciously *not* invest further in consolidation machinery
rather than gold-plate it. **[Q9]** per-bank webhook closure.

---

## Open-question dependency index (maps to doc 08)

| Q# (doc 06 §10) | Blocks | Must resolve before |
|---|---|---|
| **Q2 — eval not yet run** | the whole engine flip | **★ Phase 2** (hard gate; run in Phase 1) |
| Q10 — migration fidelity / method | data cutover | Phase 2 |
| Q1 — promotion gate split | auto-promote to `shared` | Phase 4 |
| Q9 — webhook per-bank registration | promotion fan-out routing | Phase 4 (design), Phase 6 (closure) |
| Q6 — proof-count on single-user box | promotion thresholds, bank-template defaults | Phase 4 / Phase 5 |
| Q3 — librarian centralisation | librarian agent design | Phase 3 (don't hard-couple), Phase 5 (split if needed) |
| Q4 — Strix accel undocumented | embed model / re-embed decision | Phase 1 (measure in eval) |
| Q5 — local structured-output invest-or-route | extraction quality ceiling | Phase 1/5 (design for recall-only) |
| Q7 — external-agent default read | CC/Cursor wiki tool exposure | Phase 5 |
| Q8 — Hindsight bundled-daemon port collision | Hermes→Hindsight wiring | Phase 1 (confirm), Phase 5 (use) |
| Q11 — YAGNI / over-built | depth-of-investment in Phase 6 | Phase 6 |

**The one non-negotiable:** Q2's δ-eval is a **hard gate before Phase 2**. Doc 06 §4
is explicit — *ship the ABC + conformance suite + dual-write migration first; flip the
default only after the eval.* Do not flip blind.
