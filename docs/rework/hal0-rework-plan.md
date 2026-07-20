# hal0 rework plan — de-scarring the launch

_Prepared 2026-07-17. Based on a five-cluster critical evaluation of the canonical
checkout at `/mnt/dev/repos/hal0-mono/hal0` (github.com/Hal0ai/hal0 @ e9639de1, v0.9.7.1).
Scope per project convention: `Hal0ai/hal0` only._

---

## 1. Diagnosis: it's iteration scars, and it's systemic

Five independent deep-dives (control-plane, API/MCP, agents/memory, config/install/update,
UI/tests/docs) each surfaced the **same seven patterns** without coordination. That
convergence is the headline: the architecture is sound, but ~3 months of fast iteration
left the codebase narrating its own history in-line instead of deleting it.

**The scar taxonomy (every cluster hit most of these):**

1. **Speculative generality wrapping exactly one concrete.** Engine-neutral `MemoryProvider`
   ABC → one real engine (Hindsight); `Provider` ABC forcing `health`/`start_cmd` stubs the
   container path never calls; multi-engine factory with dead `mem0`/`cognee`/`PgVector`
   branches; three agent drivers (hermes/pi_coder/opencode) where only one installs; a
   ~940-line OpenRouter **budget/spend-cap** for a paid inference path that isn't shipped —
   on a *free-local-inference* box.
2. **Past-sunset deprecation shims kept load-bearing.** `CapabilitySelection.backend`
   documented "removed v0.3" still dual-written everywhere at **v0.9.7**; `SlotConfig` still
   carries `backend`/`provider`/`runtime` deprecations with validators; `cognee==1.0.7` still
   a **core** dependency (pulls lancedb+kuzu+aiosqlite) with **0 live imports**.
3. **Double-bookkeeping the same state.** `capabilities.toml` stores the exact fields already
   authoritative in `slots/*.toml`, so an entire `SlotConfigStore` ChangeSet/commit/revert
   machine + migrations exist only to stop two copies from disagreeing. **Three** apply
   engines (capabilities orchestrator, SlotConfigStore, stacks/apply). **Two** memory engines
   (Hindsight + Honcho) kept consistent by a 582-line bidirectional sync job.
4. **God files.** `agents/hermes_provision.py` **5,305** (52 commits in 3mo — highest churn),
   `slots/manager.py` **4,087** (8 responsibilities), `config/schema.py` **3,045**,
   `api/routes/models.py` **2,509**, `api/routes/slots.py` **1,846**, `ui/dash/settings.jsx`
   **2,598**, `ui/dash/slot-modals.jsx` **2,190**.
5. **Duplicated logic that can silently diverge.** Two full OpenAI tool-calling loops
   (`board_chat` + `omni_router`); the memory plugin maintained in **three** places (two
   byte-identical + a TS reimpl); the HF client split across two route files; **four**
   device↔backend translators across 43 call sites.
6. **Doc-vs-code drift (docs are unreliable, as you warned).** 19 ADRs cited **500+ times**,
   **one** file actually exists (`0023`). ARCHITECTURE/AGENTS/CONTEXT all say "15-phase"
   Hermes; code has **18**. `hermes_provision.py`'s own module docstring still says "every
   phase is a no-op stub" atop 5,305 real lines. ARCHITECTURE points to `api/agents/skills.py`
   which **doesn't exist**. cognee "removed in ADR-0023" per doc; still a hard dep per code.
7. **Process ceremony sized for a company, not a single-box appliance.** Weekly automated
   Hermes-SDK-drift PRs + a bespoke "δ-harness/γ-suite" ritual; a self-update path with cosign
   keyless-OIDC + Sigstore + a transition-window detached-sig fallback + nightly channel +
   **three** parallel update mechanisms (tarball, git-pull, editable) for one LXC container;
   and **45 parallel git worktrees** of half-done cleanup (`hal0-01-deadcode` … `-10-ci`,
   14× `wt-fix-*`), **none carrying commits ahead of HEAD** — the cleanup has been *started*
   ~15 times and *landed* zero times. That fragmentation is itself a top-tier scar.

**The core is small.** Strip the hedges and hal0 is: a FastAPI control plane that renders
systemd+podman slot units, a dispatcher that routes `/v1`, a registry, a dashboard, and a
narrow bundled-agent integration. The agents+memory subsystem alone is ~14k lines doing the
job of ~1.5–2k. Most of this plan is **deletion**, not construction.

---

## 2. Guiding principles

- **Delete before you refactor.** The highest reduction-per-risk changes are removals. Do them first; the god files shrink on their own once the dead paths and dupes are gone.
- **Collapse to one.** One config truth, one memory engine, one tool-loop, one apply engine, one update path, one HF client, one dashboard root. Every "one of N" in the taxonomy is a deletion target.
- **Narrow every abstraction to its single concrete.** Reintroduce generality only when a *second* concrete actually ships (second engine, second agent, paid path). Encode this as a rule so it doesn't regrow.
- **One integration branch, not 45 worktrees.** Consolidate the in-flight cleanup into a single sequenced epic. Freeze new worktrees.
- **Docs are suspect until reconciled.** Treat ARCHITECTURE/CONTEXT/AGENTS as claims; verify against code as you touch each area, then collapse to one authoritative doc.
- **"Some downtime OK" is a tool, not a default.** Use the tolerated migration window for the two changes that genuinely need it (config-truth collapse, memory-engine collapse); keep everything else incrementally shippable.

---

## 3. Phased plan

### Phase 0 — Consolidate the workstream (unblock everything)

The 45 worktrees are why nothing lands. Before writing code:

- **Triage & harvest** the `hal0-bones-0X` and `wt-fix-*` branches: each was a real intent
  (deadcode, memory, slots, cli-consolidate, cli-footguns, installer, mcp, updater). Diff each
  vs HEAD, salvage anything not already superseded into the plan below, then **prune all of
  them** (`git worktree prune` + delete branches). Reclaims ~12 GB and removes the illusion of
  progress-in-flight. Also prune the ~30 `.claude/worktrees/` agent branches.
- **Cut one long-lived branch** `rework/descar` off HEAD. All phases land as small PRs into it;
  it merges to main at phase boundaries. No parallel cleanup branches.
- **Lay the sunset-shim CI guardrail now** (so Phase 1 can delete aggressively without regrowth):
  a `HAL0-SUNSET: vX.Y.Z` marker convention (CI fails when a marker goes overdue) + a **debt
  ratchet** (`scripts/scar_baseline.txt` — the current `legacy/deprecated/removed-in` count; CI
  fails if it rises). Baselined to pass green on day one; each Phase 1 deletion lowers the baseline.
  See `/home/mint/hal0-phase0-handoff.md` for the concrete handoff prompt.
- **Snapshot the live box** (lxc105 is mid-backup already — good) so the downtime-tolerant
  phases have a rollback point.

### Phase 1 — Pure-deletion de-scar (low risk, high volume, ship incrementally)

Each item is independently shippable and mostly mechanical. Rough deletable footprint noted.

| Target | Action | ~LOC / weight |
|---|---|---|
| `cognee==1.0.7` core dep | Drop from `pyproject.toml`; delete dead `mem0`/`PgVector`/`cognee` branches in `memory/__init__.py`, `provider.py` | dep + lancedb/kuzu/aiosqlite transitive weight |
| OpenRouter budget gating | Delete `agents/budget.py` + `api/agents/budget.py` (no paid path shipped; reintroduce with the feature) | ~940 |
| Speculative agent drivers | Delete `agents/pi_coder/` + `agents/opencode/` drivers & duplicated TS plugins (uninstallable in v0.3; land with the unlock) | ~1,225 |
| Duplicate memory plugin | Keep one canonical Python copy; symlink/generate the installer copy; delete the `src/` duplicate + stale self-contradicting docstring | ~few hundred |
| Config migration framework | Delete the versioned framework whose only migration is an identity no-op (`migrations/v1.py return data`) **or** actually use it for Phase 2's shims — not both | ~200 |
| `handoffs/` (18 dated dumps, 362 KB) | Move out of the tree to `docs/archive/` or a separate notes repo; mark non-authoritative | 362 KB |
| CHANGELOG.md (81 KB / 1,969 lines) | Truncate to recent releases; older history lives in git tags | 81 KB |
| Ghost ADR citations | Either backfill the ~18 missing ADRs from git history, or strip `ADR-000X` citations to inline rationale. No dangling references. | 500+ citations |
| Dead provider stubs | Remove the compliance-only `health`/`start_cmd`/`build_env` stubs from the spec providers (narrow the ABC — see Phase 3); keep the live `container_spec` paths | ~hundreds |
| Doc drift quick-fixes | Fix "15-phase"→18, the "no-op stub" docstring, the `skills.py` phantom path, cognee-removed claim — as you touch each area | — |

**Guardrail:** landed in **Phase 0** (sunset-marker check + scar-count ratchet). Every Phase 1
deletion PR lowers `scripts/scar_baseline.txt`; anything deferred gets a `HAL0-SUNSET:` marker
instead of an untracked comment. Stops scar #2/#6 from regrowing.

### Phase 2 — Collapse duplicated truths (structural; use the downtime window)

These are the changes that remove whole *classes* of bug. Two need a migration window.

1. **One config truth: make `capabilities.toml` a derived projection over `slots/*.toml`**
   (or delete it, deriving children from slot `type`). This removes the `SlotConfigStore`
   ChangeSet/commit/revert/rollback machine, the "can never be half-reconciled" complexity,
   the `capabilities migrate` drift tool, and the "2026-05-20 production drift" bug class.
   Pick **one** apply engine (SlotConfigStore) and delete `stacks/apply` + the capabilities
   orchestrator apply path. *(needs migration window)*
2. **One memory engine.** Decide Hindsight **or** Honcho (see §5 — the live box already runs
   Honcho). Delete the loser, the 582-line `honcho_migrate.py` bidirectional sync + `hal0
   memory sync-graph`, the per-agent engine-routing config, and collapse the `MemoryProvider`
   ABC/factory into the one concrete provider. *(needs migration window)*
3. **`device` as sole truth.** ⚠️ **SUPERSEDED — read the "P2-device — CORRECTION" block below (§ near L1174) and §23.1; this original text is WRONG.** The corrected scope: delete ONLY `device_to_legacy_backend` (Concept-A mirror); KEEP `canonical_device`/`device_to_backend`/`map_backend_to_device` (Concept-B runtime token `rocm|vulkan|cpu|flm`, still device-derived and load-bearing for SlotCard/NPU-dispatch/argv); strip 5 dual-write sites + 2 fields (`SlotConfig.backend`, `CapabilitySelection.backend`); and CONVERT (not delete) both `_promote_backend_to_device` validators to read-only promote-then-drop shims — deleting them regresses legacy cpu/npu slots to gpu-rocm (no slot-TOML migration exists). Not "pure deletion."
4. **One tool-calling loop.** Extract `hal0/tool_loop/` (extractors, `role=tool` builder,
   `openai_tool_schema()`, abstract loop parameterized by injected llm/dispatch/budget/sink).
   `board_chat` keeps only its SSE sink + approval pause + text-parse fallback; `omni_router`
   keeps parallel dispatch + delegation guard. Then collapse `board_chat`'s three self-HTTP
   dispatch resolvers onto the shared admin catalog.
5. **One HF client.** Consolidate `models.py::_fetch_hf_repo` + `hf.py::_fetch_hf_search` into
   `hal0/upstreams/huggingface.py`; both routers call it.
6. **One update path.** For a single LXC: keep one channel + one signing scheme; delete the
   nightly channel, the transition-window detached-sig fallback, the prepare/commit two-phase,
   and fold `_update_via_git`/editable into a single dev-mode flag.
7. **De-pseudo the composite `hal0` upstream.** Build `/v1/models` aggregation as a direct read
   over slot state instead of a fake registry entry that must be skip-guarded at 4 dispatch
   steps.

### Phase 3 — Decompose the god files (selective rewrite of the worst)

By now the dead paths and dupes are gone, so these shrink before you split them.

1. **`hermes_provision.py` 5,305 → ~200-line idempotent installer.** The bulk is defensive
   host-wrangling (uv venv, chown trees, ownership-reconcile phases that *undo damage earlier
   phases cause*, foreign-gateway detection, home adoption). Replace the resumable 18-phase
   checkpoint pipeline with: resolve Python → `uv venv` + install pinned SDK → render one
   `config.yaml` → drop a systemd unit. Move `hal0-brain` seeding (2 phases) and identity-card
   publishing out of the installer into the API lifespan where the memory layer is known-up.
   This is the single biggest structural win and kills the highest-churn file.
2. **`slots/manager.py` 4,087 → core + collaborators.** Keep the state machine + systemd/podman
   lifecycle as the core. Extract: the capacity manager (idle/sweep/pressure-evict loops), the
   config-drift comparator (likely deletable — running argv equals rendered argv by
   construction), the NPU-trio reconciler (consolidate its shadow-lifecycle into one `npu/`
   package), and move the model-fallback *guessing* heuristics back to the registry/discovery
   layer (or replace with the structured error the boundary already prescribes).
3. **`config/schema.py` 3,045 → schema + data.** Externalize the 25 `SEED_PROFILES`/`SEED_STACKS`
   + image pins + bench numbers to shipped TOML under `share/`; keep only derivation logic in
   Python. Split `SlotConfig` into identity vs runtime. Consider `extra="forbid"` on leaf models
   with an explicit escape-hatch table so the schema actually validates. Roughly halves the file
   and turns flag-tuning into a data edit, not a release.
4. **Thin the mega-routers.** Extract `models.py`'s HF client + async pull-job manager and
   `slots.py`'s systemd/cgroup/llama-metrics adapters into service modules
   (`registry/`, `slots/metrics.py`); routers become request→service→envelope shells. Convert
   the 38 hand-rolled `request.json()` sites to Pydantic bodies; convert the comfyui/benchmarks
   outliers to typed `Hal0Error` raises. Auto-generate the MCP admin `_REST_MAP`/`_PATH_ARGS`
   from the FastAPI route table, keeping only the security overlay hand-authored.
5. **UI: finish the stalled "Phase B" ES-module migration.** Kill the `window`-globals shim in
   `main.tsx` so Vite has a real module graph and can tree-shake; gate `mock.ts` (1,193 lines)
   behind `import.meta.env.DEV` so it stops shipping in prod; collapse the CSS eras
   (`dashboard.css`/`overhaul.css`/`redesign.css`/`memory-overhaul.css`, ~10.5k lines) into one
   system; then split `settings.jsx`/`slot-modals.jsx`. Resolve the dual-root
   (`main.tsx` + `dash/main.jsx` + `dashboard-redesign.jsx`) to one render.

### Phase 4 — Right-size the process (stop re-scarring)

- **Tests:** add `integration`/`requires_podman` markers, tag the 92 systemctl/podman shell-out
  tests, split CI fast-vs-box tiers (the `hal0-10-ci` intent). Add a thin real-podman
  integration tier so the mock-heavy suite (216 mock files, ~1:1 test:code) doesn't make every
  refactor break on structure instead of behavior.
- **Docs:** collapse ARCHITECTURE/CONTEXT/AGENTS (heavy overlap) to one authoritative doc; adopt
  a rule that a decision is either a real ADR file or inline rationale — never a citation to a
  non-existent doc.
- **Upstream Hermes:** drop the weekly automated drift PRs + δ/γ ritual; vendor a frozen SDK and
  bump on-demand, or talk to Hermes purely over its HTTP/JSON-RPC surface (which `chat_proxy`
  already does) and delete the shim layer.
- **Codify the anti-scar rules** (sunset-shim CI check, "narrow abstraction to one concrete,"
  "one config truth," "land on one branch") in CONTRIBUTING so the next iteration cycle doesn't
  rebuild the debt.

---

## 4. Sequencing & risk

```
Phase 0  worktree consolidation        │ 0 product risk, unblocks everything      │ do first
Phase 1  pure deletion                 │ low risk, ship each PR to main            │ fast, parallelizable
Phase 2  collapse truths (config/mem)  │ med risk, 2 items need downtime window    │ the class-of-bug killers
Phase 3  decompose god files           │ med risk, easier after 1+2 shrink them    │ the sustained-velocity payoff
Phase 4  process guardrails            │ low risk                                  │ prevents recurrence
```

Do **Phase 0 → 1** before deciding anything else — the deletions will clarify how much of
Phases 2–3 is even left. Phase 1 alone likely removes several thousand lines and one heavy
dependency with near-zero product risk.

---

## 5. Decisions

**Resolved (2026-07-17):**

- ✅ **Memory: Hindsight. Remove Honcho entirely.** Delete the Honcho provider, the 582-line
  `honcho_migrate.py` bidirectional sync, `hal0 memory sync-graph`, the per-agent engine-routing
  config, and the `feat/honcho-*` worktrees. Collapse `MemoryProvider` ABC → concrete
  `HindsightProvider`. **NOTE:** the live lxc105 box currently self-hosts Honcho as its store, so
  this needs a **one-time Honcho→Hindsight data migration** first (the existing
  `migrate_honcho_to_hindsight` direction is reusable for exactly this, then deleted).
- ✅ **Keep OpenWebUI** (reversed 2026-07-17) — it's the easy bootstrap/setup surface. Stays a
  first-class companion service. (Still worth a light pass to ensure its env-writer + systemd unit
  survive the Podman/Quadlet + perms changes in §7.2.)
- ✅ **Keep Hermes (the one bundled agent) — but fix its memory** with proper Hindsight plugins
  (see §7.4). Simplify its provisioner (§7.2/Phase 3.1) but do **not** remove it.
- ✅ **Delete the rest of the speculative agent surface:** `pi_coder` + `opencode` drivers/plugins,
  the ~940-line OpenRouter budget/spend-cap, and the persona JSON-RPC hot-reload. Keep only Hermes.
- ✅ **ComfyUI (image) + TTS stay first-class.** No companion services removed (OpenWebUI kept).
- ✅ **State storage: adopt SQLite for runtime + registry, TOML for config (§7.5, plan in §8).**
  Pilot = the model-metadata registry, built as part of §7.1; runtime-state migration deferred to
  Phase 3 with the `slots/manager` split.
- ✅ **Update mechanism: Model B (public, signed auto-update) — but ONE scheme, not three (§7.7).**
  Keep: cosign keyless-OIDC + Sigstore, stable channel, versioned dirs + atomic symlink swap +
  `hal0.previous` rollback. Remove: nightly channel, transition-window detached-sig fallback,
  parallel `_update_via_git` (→ dev-only flag), editable version-drift machinery, prepare/commit
  two-phase (unless a real partial-update bug requires it). ~2,159 → ~600–800 lines.

**All major decisions resolved.** Remaining choices are implementation-level and made as work lands.

---

## 6. Immediate next steps

1. You confirm the remaining §5 items (esp. the pi_coder/opencode/budget deletion + SQLite).
2. I do Phase 0: triage/harvest the 45 worktrees into this plan, then prune.
3. I open the `rework/descar` branch and land Phase 1 deletions (Honcho removal after the one-time
   data migration; pi_coder/opencode/budget/hot-reload; dead providers/shims) as small reviewable
   PRs, with the sunset-shim CI guardrail first so nothing regrows. OpenWebUI stays.
4. We reassess Phase 2 scope once the deletions land.

---

## 7. Product-shaped reworks (from the six field notes)

These are the *feature-shaped* pain areas, verified against code. They thread into the phases
above (deletion in P1, structural collapse in P2, decomposition in P3) but are grouped here by
product concern. **The through-line: notes #1, #5, #6 are all one missing abstraction — the
model doesn't own its own config.**

### 7.1 Model-owned config: flags, runners, pulling (notes #1, #5, #6)

Today the model is a dumb path; its flags live in profiles, its runner-image in profile pins +
per-provider hardcodes, its file layout in a single-file assumption. Fix: **the model owns its
inference requirements** via a per-model metadata record; profiles/runners/slots become
resolution *inputs*. This is **already half-built** — `ModelDefaults.profile`,
`_apply_preferred_profile` (`slots/manager.py:2454`), `Model.mmproj`, `Model.backends` all exist
and are unwired. This is completion, not a rewrite.

> **📦 MODEL-LAYER EPIC — one coordinated workstream (7.1a–e + SQLite).**
> These are **not separate tasks** — they share one data record and one storage backend, so they
> land together or you do throwaway work. Build order:
>
> 1. **Registry record + SQLite `files`/`models`/`revision` tables** (§7.1e + §7.5 pilot) — the
>    schema everything hangs off (content-addressed, **refcounted** file rows). First.
> 2. **File-SET pulling** (§7.1c) — recursive HF enumeration, multi-shard, deterministic mmproj,
>    revision pin; writes the repo/revision-addressed layout; hardlink-dedup via refcount.
> 3. **Unified store resolver + repo/revision layout** (§7.1e) — one read/write resolver, `path`
>    derived from `(repo,revision,file)`, atomic set-swap, real GC, store permissions.
> 4. **Runner-image registry** (§7.1b) — `RUNNER_IMAGES`, explicit `runtime_family`, `preferred_runner`.
> 5. **Flag resolution** (§7.1a) — model capability flags (`mtp`/`jinja`); profiles shrink to tunes.
> 6. **Taxonomy untangle** (§7.1d) — modality/capabilities/tags split; kill the `labels` routing gate.
>
> Steps 1–3 are the foundational/risky core (Phase 2/3, downtime-tolerant); 4–6 layer on cleanly once
> the record exists. Ship behind the existing registry interface so callers don't change.

**7.1a — Flag resolution.** Final argv is 7 last-wins segments (`container.py:561`/`resolve_argv`).
The merge algorithm is fine; the problem is *which layer owns each flag*:
- `-ngl` is set in **4** places; MTP logic is spread across **6** sites (schema + container +
  model_meta name-regex sniffing); `--jinja` has **no negation**, which forces byte-identical
  `*-nojinja` duplicate profiles (`schema.py:967,979`); `*-small` clones likewise.
- **Target:** add `ModelDefaults.mtp: bool` + `ModelDefaults.jinja: bool` capability flags →
  delete the name-regex MTP sniffing and the `*-nojinja`/`*-small` profile clones. **Profiles
  survive** (your call was right) but shrink to *pure hardware/backend tunes* (`-dev`, `-b/-ub`,
  `--threads`, `-fa`, KV-quant, `-ngl`) and **lose `image` + `mtp`**. "thinking"/`enable_thinking`
  is a request-dispatch concern, **not** a launch flag — leave it in `normalize/thinking.py`.
- Precedence (low→high): **runner image → profile tune → arch defaults (`FAMILY_DEFAULTS`, keyed
  off registry architecture not filename) → per-model metadata (mtp/jinja/mmproj/extra_args) →
  slot instance overrides (port/ctx/parallel/vision/`[server].extra_args` always wins).**

**7.1b — Runner-image registry.** "Which runner" is currently decided by **string-sniffing the
image tag** (`profiles/__init__.py:106`), and image refs resolve through **3 inconsistent chains**
(llama honors `slot.image`, FLM ignores it entirely, kokoro's `image_ref` is dead) with the FLM
image **triple-pinned** (seed + `flm.py` + manifest).
- **Target:** one `hal0/runners/` registry — `RUNNER_IMAGES: {key → {image, runtime_family,
  supports:{mtp,jinja,mmproj}, device_class}}` — absorbing `DEFAULT_ROCMFPX_IMAGE`, every provider
  `_DEFAULT_*_IMAGE`, and the manifest digest pins. `runtime_family` becomes an **explicit lookup,
  not a sniff**. Add `Model.preferred_runner` (a key into the registry). Add
  `_apply_preferred_runner` beside `_apply_preferred_profile`. This is your "models define their
  preferred runner from a list" note, exactly.

**7.1c — Model pulling.** The engine is a solid *single-file* streamer bolted onto a data model
that **cannot represent a multi-file model**. Confirmed defects: shards are **deleted on sight** by
discovery (`discover.py:158`); the inspect tree walk is **non-recursive/unpaginated** (misses
subdir + multi-page repos); mmproj pairing is `rglob`-order roulette (`setdefault` first-wins);
updates re-pull one file at floating `main` with **no revision pin** and **drop mmproj entirely**.
- **Target:** a **file-*set* abstraction end-to-end** — registry gains `revision`, `files[]`,
  `shards[]` (shard-1 is the entry point) + per-file sha/role; recursive+paginated HF enumeration;
  multi-shard download; deterministic mmproj (pair by prefix, tie-break by precision); record the
  resolved commit sha so re-pull detects upstream updates over the **whole set**. Reuse the
  existing per-file resume/integrity core; add a set-level completion gate.

**→ The metadata record is identical across 7.1a/b/c** (`source_repo`, `revision`, `files[]`,
`shards[]`, `mmproj`, `preferred_runner`, `mtp`, `jinja`, `defaults{profile,extra_args,ngl,ctx}`).
That relational record is why **the registry is the natural SQLite pilot (§7.5).**

### 7.1d — Capability / modality taxonomy (untangle "capability")

**Problem (verified):** "capability/type" is **9+ overlapping axes**, and `vision` alone spans **9**
of them with no unifying normalizer. Worse, there are three hidden axes: **`model.labels`** (the
*actual* routing gate), **`SlotConfig.vision`** (a bool), and **`CuratedModel.capability`** + the
installer's own vocab. Live bugs this causes:
- 🔴 **tags→labels disconnect.** `omni_router/filter.py` + `providers/flm.py` route on hand-authored
  `[model].labels` (`tool-calling`/`reasoning`/`vision`); **nothing copies `Model.tags`→`labels`**.
  A model *tagged* `tool-calling` ships **no tools**. This is the top correctness fix here.
- **`moe` is vestigial** — in the UI toggle set + `/api/meta/enums`, consumed nowhere (`is_moe` at
  `hardware/recommend.py:122` uses `mtp`/`a3b`-in-id and ignores the tag).
- **4 spellings for ASR** (`asr`/`stt`/`transcription`/curated `stt`), plus `embed`/`embedding`,
  `rerank`/`reranking`, `image`/`img` — bridged by scattered alias maps, no single normalizer.

**Target: 3 model axes + 1 runtime axis, single-source, canonical spellings, one normalizer.**

```python
# A. MODALITY — dispatch/routing. Closed enum, ONE canonical spelling, derived-first.
class Modality(str, Enum):
    CHAT="chat"; VISION="vision"; EMBED="embed"; RERANK="rerank"
    ASR="asr"; TTS="tts"; IMAGE="image"; VIDEO="video"
# normalize_modality() folds every alias (stt/transcription→asr, embedding→embed,
#   reranking→rerank, img→image) at ingest. slot `type` (llm/embedding/…) becomes a
#   DERIVED PROJECTION of modality, not an independent vocab.

# B. CAPABILITIES — launch/runtime flags. Typed bools, gated by runner.supports.
class ModelCapabilities(BaseModel):
    mtp:          bool | None = None    # → --spec-* MTP bundle
    jinja:        bool | None = None    # → --jinja
    tool_calling: bool | None = None    # → routing gate (REPLACES model.labels)
    # reasoning REMOVED — request-time (enable_thinking), optionally a tag

# C. TAGS — free-text UX descriptors ("coder","reasoning", domains). Inert; drive nothing.

class Model(BaseModel):
    architecture:  str | None = None                 # → moe/dense profile + FAMILY_DEFAULTS (replaces the dead `moe` tag)
    modalities:    list[Modality] = []               # derived-first (vision⟸mmproj, embed/rerank/asr⟸runner role)
    capabilities:  ModelCapabilities = ModelCapabilities()
    tags:          list[str] = []
    preferred_runner: str | None = None ; mmproj: str | None = None
```

**Enforced rules that kill the current bug classes:**
1. **Routing reads `capabilities.tool_calling` (a real bool), not `model.labels`.** Delete the
   `labels` axis (or make it a *derived read* of capabilities/modalities) — closes the 🔴 footgun.
2. **Modalities recomputed on pull/swap** from facts (mmproj⟹vision, runner role⟹embed/rerank/asr),
   never hand-authored except an explicit override list → routing can't disagree with reality.
3. **Toggle visibility = `capability settable ∧ capability ∈ runner.supports`** → no MTP toggle for
   a runner that can't speculate.
4. **`SlotConfig.vision` bool → an *override*** (suppress derived `--mmproj`), not a 4th source of
   the word. **`moe` tag deleted**; dense/moe comes from `architecture`. **`CuratedModel.capability`
   + installer vocab fold into `Modality`.**
5. **`/api/meta/enums` exposes the one canonical set**; `normalize_modality()` is the single
   ingest-time folder. Rename the `capabilities/` **package** (e.g. `slot_cards/`) so "capability"
   stops meaning four things.

This collapses ~9 axes into: **modality (routes) · capabilities (flags) · tags (decorate) · device+runner (where it runs)** — all single-source on the model record.

### 7.1e — Model store, storage dirs, edit/delete lifecycle

**Verified problems, ranked:**

1. 🔴 **Two divergent store resolvers → pull writes where the container can't read.**
   `paths.model_store_root()` (`config/paths.py:143-175`, used for **container mounts**) falls back
   to `/mnt/ai-models`; `pull._pull_root()` (`registry/pull.py:224-240`, used for **writing**) falls
   back to `/var/lib/hal0/models`. When `[models].store` is unset they **disagree by design** — the
   pull writes an absolute `path` the read-only container mount doesn't contain → silent "model file
   not found" at load. This is the top defect: a config-shaped trap baked into every registry row.
2. **Store migration orphans every existing row.** `_apply_store_change` (`api/routes/settings.py:458-564`)
   moves files old→new then relies on `scan_and_register` to "fix stale paths" — but scan **skips on
   id-collision and never rewrites an existing id's `path`** (`discover.py:382-389`). The reassuring
   comment is wrong; post-migration every pre-existing slot fails "file not found."
3. **Delete never removes bytes + no GC + auto-scan resurrection.** `DELETE` is registry-only
   (`routes/models.py:1420`); there's zero GC of unreferenced files; `auto_scan_on_start`
   re-registers anything left on disk. "Delete" neither reclaims space nor is durable — multi-GB
   leaks accumulate.
4. **No dedup — copy-always.** Every quant/shard/mmproj/variant of one repo is a full separate copy
   under its own id-dir; schema even hints at symlinks (`model.py:93`) but nothing creates them.
   Multiplies NFS footprint.
5. **Rows are never reconciled against disk.** No stat-on-list, no "missing file" flag, no reverse
   prune; mtime-only cache has a same-second-write blind spot for external editors. The registry can
   silently lie about what's loadable.
6. **No id rename; dir stays named after the old id.** `update()` forbids id change; rename = delete
   + add (non-atomic) and leaves `<store>/<old-id>/` — id and directory diverge permanently.
7. **Store permissions unmanaged on the shared NFS mount + `:z` relabel on NFS.** No PermRow for the
   model tree (`install/perms.py:143-189`); pulled files get no explicit chmod/chown/group, and
   `selinux="z"` (`container.py:709`) recursively relabels an **NFS** source — slow, and fights other
   hosts' labels. This is exactly the "other users/containers can't read pulled files" class in your
   `ai-models access model` memory. Ties into §7.2 (one `hal0`-user ownership model).
8. **Multi-file update has no atomic set-swap.** Main GGUF is `os.replace`d, *then* mmproj downloads
   (`pull.py:1023-1035`) — a window where a reloading vision slot gets a mismatched main/mmproj pair.
   (Note: single-file update-in-place *is* atomic + safe — a running slot keeps the old inode.)

Also confirmed **doc drift**: ARCHITECTURE.md claims load-time "GGUF magic-byte detect + HF-cache
repo-name fallback" — neither exists at load; `_resolve_model_path` (`container.py:323`) just returns
the stored path and can't recover a stale one. Magic-byte detection is registration-time only.

**Target design (these are all ONE change — store + file-set + SQLite land together):**

- **Unify the store** into one `Store` resolver (identical fallback on read + write); **assert at
  slot-launch that `model.path` is under `model_store_root()`** and fail fast with a clear error
  instead of a silent container miss.
- **Repo/revision-addressed layout** (HF-cache style `models--<repo>/snapshots/<rev>/<file>`); make
  `Model.path` a **derived** view over `(repo, revision, file)`, not a stored absolute string. Then
  rename touches only the id, migration just moves the tree (paths recompute), and re-pull of a new
  revision is a new snapshot dir + **atomic pointer flip** (which also gives the atomic set-swap that
  fixes #8).
- **Model owns a file SET** (`files[]`/`shards[]`/`mmproj`/`revision`, roles per file) — the §7.1c
  abstraction. Enables: delete enumerates exactly its files; **refcount a file across models** →
  hardlink dedup (#4) and safe delete; well-defined set-swap (#8).
- **Real GC + reconciliation:** `list_models` lazily flags `path_missing` (cheap cached stat); a
  `POST /api/models/gc` prunes rows with missing files and (opt-in, refcount-guarded) deletes
  unreferenced bytes; delete gains `delete_files=true` guarded by refcount. Fix migration to
  **prefix-rewrite each row's `path`** (deterministic), not rescan.
- **Disk budgeting:** per-store max bytes/max models; a **shared in-flight reservation** so N
  concurrent pulls preflight against `free − Σreserved` (today they race, `pull.py:832`).
- **Clean permissions:** post-pull land files `0644 hal0:<shared-group>` under a **setgid** store
  dir; use `:Z` or skip relabel for detected NFS sources. (Fixes the `ai-models` readability class.)

**→ The SQLite pilot (§7.5) is the enabler, not a separate step.** The flat TOML registry can't
express refcounts, file-sets, or "rows with missing files" queries. SQLite carries a `files` table
(content-addressed, refcounted) + `models` + `revision` — which is *exactly* what dedup, GC,
atomic set-swap, and rename-without-move require. **§7.1 (file-set) + §7.5 (SQLite) + this store
rework are the same change and must land together** — retrofitting file-sets onto flat TOML is
throwaway work.

### 7.2 Container runtime & permissions (notes #2, #3)

- **Podman. Definitively.** Current: Podman, rootful, run as `root`, daemonless CLI shell-out
  (docker is only a silent fallback + iptables antagonist). Keep it — daemonless, `Type=notify`
  sd_notify, SELinux `:z`, native `--device` GPU passthrough (ROCm/NPU) + NVIDIA CDI. **Migrate
  slot units to Quadlet `.container` files**, which deletes the hand-rendered `podman run` string
  assembly + `[Unit]/[Service]` skeleton + write→daemon-reload→enable dance in `container.py`.
  Delete the misleading docker-referencing `.hal0ai` slot template; treat docker as unsupported.
- **Perms: one root cause, one fix.** The recurring root-vs-`hal0` bug is structural: the
  provisioner runs **as root writing `root:root` files** that the `User=hal0` runtime must own →
  a fix-clobber-refix cycle (`home_init` chown → mid-phases write root files → `ownership_reconcile`
  re-chowns). `hal0-api` itself "always runs as root" now (the hardened flip was reverted),
  forcing the `UMask=0002` kludge. The declarative `OwnershipStore` (`install/perms.py`) exists
  but **ships inert**.
- **Ship state:** drop privileges to `hal0` **before** the config-writing phases (files born
  `hal0:hal0`) → `_chown_tree_to_hal0` + `_phase_ownership_reconcile` become **dead code**. Run
  `hal0-api` as `User=hal0`; isolate the few genuinely-privileged ops (write
  `/etc/systemd/system/hal0-slot@*.service`, `daemon-reload`, iptables) behind one `sudo -n`/polkit
  helper. Ownership map: `/usr/lib/hal0` `root:root 0755` ro · `/etc/hal0` `hal0:hal0 2775` setgid
  (secrets `root:root 0600`) · `/var/lib/hal0` incl. `HERMES_HOME=/var/lib/hal0/.hermes`
  `hal0:hal0` (`agents/` `0711`). **Adopt `OwnershipStore` as the single declarative truth**,
  default user `hal0`.

### 7.3 hal0-brain as a first-class subsystem (note #4)

Today brain is a good feature in a Hermes costume: persona store under `.hermes/personas`, identity
literally prefixed `hermes__hal0-brain`, all 3 provisioning steps are **phases inside the Hermes
installer**, and it reimplements the tool-loop OmniRouter already has.
- **Target:** new `src/hal0/brain/` module (service/tools/identity/provision), **provisioned by the
  hal0 API lifespan on every boot** — not Hermes phases (delete `_phase_persona_seed` brain half,
  `_phase_brain_profile_seed`, `_phase_brain_profile_mcp_wire`). Identity `hal0-brain` (drop the
  `hermes__` prefix), memory `private:hal0-brain`. **Uses the shared tool-loop core** (§7.6). Move
  the persona store out of `.hermes` to a hal0-owned path.
- **Reliability:** the 1B `brain` slot can't emit native tool calls on the FPX runtime, and its
  `tool_model`/`agent` fallback ships **disabled + model-less**. Default `tool_model="hal0/agent"`,
  ensure that slot is warm when `brain_chat.enabled`, and add a **startup readiness gate** that
  degrades brain to read-only rather than 500-ing mid-turn (mirror the WARMING watchdog in
  `e9639de1`). Brain is listed as **system infrastructure**, not an installed agent.

### 7.4 Hermes: keep, fix its memory (your decision)

Hermes stays as the single bundled agent. Two fixes:
- **Memory via proper Hindsight plugins.** ⚠️ **CORRECTED by §18.1 HP-1 + §23.1:** hal0-memory is
  **2 copies BY DESIGN** (importable source `src/hal0/agents/hermes/plugins/memory_hindsight/` +
  hyphen seed `installer/agents/hermes/plugins/hal0-memory/`), **byte-identical and parity-test-locked**
  (`test_seed_parity.py`) — **KEEP BOTH, this is NOT 3→1 drift** (the pi_coder TS reimpl is already
  gone). Real work: fix the 2 stale docstrings that falsely claim `memory_hindsight` was deleted; rename
  tools `hal0_memory_*` → upstream `hindsight_retain/recall/reflect` (keep hal0's `shared` param); add
  `get_config_schema`/`save_config` (`~/.hermes/hindsight/config.json` `local_external`). Standardize
  wiring on Hindsight (Honcho gone). Give Hermes a clean `private:hermes` bank through the same MCP.
- **Slim the provisioner.** `hermes_provision.py` (5,305 lines / 18 phases / 52 commits) → a
  ~200-line idempotent `install_hermes(*, repair=False) -> InstallReport` (resolve Python → `uv venv` +
  install pinned SDK → dir-drop plugins → apply config via **`hermes config migrate` + `hermes config
  set` + `overrides.yaml` deep-merge** → render context files (SOUL/HERMES/AGENTS Jinja) → drop unit →
  gateway secrets → smoke). ⚠️ **NOT a whole-file `config.yaml` Jinja render** — `config.yaml.j2` is
  already deleted and a full render clobbers `image_gen.provider`/`tts.provider` on migrate (§18 gotcha).
  Idempotency = every step a **converging write**, not a checkpoint pipeline. The checkpoint/
  ownership-reconcile framework disappears once perms are **born-owned** — the load-bearing contract is
  **drop privileges to `hal0` BEFORE any config-writing step** so files are born `hal0:hal0` (§7.2/§23.3),
  and brain phases move out (§7.3). **Hard prereq: P3-perms (`OwnershipStore` default `hal0` + drop-to-hal0
  + one privileged-ops helper) lands FIRST** — the installer can't delete the chown phases until it does.
  The `hal0-agent@.service` unit is **unchanged** (already `User=hal0`); installer only ships drop-in +
  `daemon-reload` + `enable --now`. Keep Hermes; kill the machinery around it.

### 7.5 State storage: SQLite for runtime + registry, TOML for config (DB decision)

Split by ownership. **Config stays TOML files** (operator-editable, greppable, git-able — the
appliance's contract): `hal0.toml`, `slots/*.toml`, `providers.toml`, `profiles.toml`,
`upstreams.toml`. **Machine-owned state moves to one embedded SQLite** (`/var/lib/hal0/hal0.db`,
WAL): slot runtime `state.json`, pull-job progress, events/journal, provision checkpoints,
decision logs, metrics, approvals. **Registry + the §7.1 model-metadata layer → SQLite** (with
`registry export` to TOML for inspection) — it's inherently relational (model↔shards↔mmproj↔
revision↔runner↔flags). This **re-homes** the hand-rolled transaction log (`SlotConfigStore`
ChangeSet/commit/revert, ~640 lines), the `slot_write_lock`, and the mtime cache onto SQLite
transactions — ⚠️ **NOT a naive delete (see §23.1):** P2-config makes `SlotConfigStore` **THE single
slot-apply engine** first; §7.5 SQLite then subsumes its ChangeSet machinery onto `BEGIN IMMEDIATE`
transactions and must **NOT** reintroduce a second slot-apply path (no resurrected `stacks/apply`
reconcile). Makes the box atomically backup-snapshottable (relevant to the PBS/FUSE backup-hang
scars). **SQLite only,
not Postgres** (Honcho's Postgres left with Honcho). A DB does **not** by itself fix
double-bookkeeping — that's still "one source of truth + derived view" (§Phase 2). **Pilot: the
model-metadata registry (§7.1)** — highest value, self-contained, lowest risk.

### 7.6 Cross-cutting: the shared tool-loop (referenced by 7.3, brain, OmniRouter)

There are **two** hand-rolled OpenAI tool-calling loops (`board_chat._chat_stream` +
`OmniRouter.run_loop`) with duplicated extraction, `role=tool` building, and text-parse fallback.
Extract one `src/hal0/toolloop/engine.py` (`run_tool_loop(llm_fn, tools, dispatch_fn, *, max_rounds,
on_event)`); brain, OmniRouter, and board_chat all call it. Fixes the toolcall-leak mitigations
**once** for all callers. (This is Phase 2 item #4, elevated because brain depends on it.)

### 7.7 Updater — Model B, one scheme (note: update mechanism decision)

Distribution model = **public, signed auto-update**. But collapse three mechanisms to one.
**Keep:** cosign keyless-OIDC `verify-blob` + Sigstore bundle (`updater.py:539`); **stable** channel;
versioned install dirs + atomic symlink swap + `hal0.previous` rollback (`updater.py:382-419`).
**Remove:** nightly channel (`:161,271`, pre-release skip `:509-538`); transition-window
detached-sig fallback (`:178-194`); parallel `_update_via_git` (`update_commands.py:280` → demote to
a dev-only `--source git` flag); editable version-drift machinery (`update_commands.py:61-121`);
prepare/commit two-phase (unless a real partial-update bug requires it — the atomic swap already
gives atomicity). Target: **~2,159 → ~600–800 lines.** Lands in Phase 1 (deletions) + Phase 2
(consolidate to one path).

---

## 8. SQLite adoption — setup plan

Scope (from §7.5): **runtime state + registry → SQLite; human-authored config stays TOML.**
Dependency-light, single-box, incremental. Pilot on the registry; runtime state follows in Phase 3.

### 8.1 Foundations (`src/hal0/db/`)

- **Library: stdlib `sqlite3`** — no new dependency (matches the de-scar ethos; no ORM). A thin
  repository layer returns/accepts the existing **pydantic** models, so validation stays where it is.
- **File:** `/var/lib/hal0/hal0.db` (Runtime tier — preserved across updates, survives uninstall
  with `--keep-data`).
- **Connection policy:** `src/hal0/db/connection.py` opens with
  `PRAGMA journal_mode=WAL; foreign_keys=ON; busy_timeout=5000; synchronous=NORMAL`. WAL lets the
  background loops (idle/sweep/pressure, pull jobs) read/write concurrently with request handlers in
  the single FastAPI process. One connection per request/task; the pragmas make that safe.
- **Schema versioning:** a `schema_migrations(version, applied_at)` table + a forward-only runner in
  `src/hal0/db/migrate.py` applying `db/migrations/NNN_*.sql` on open. **This is the real job the
  currently-no-op config migration framework (§Phase 1) was pretending to do** — repurpose the
  concept here where it's actually needed.
- **Backup:** document `VACUUM INTO '/backup/hal0.db'` (or `.backup`) for an atomic snapshot — one
  file replaces N concurrently-written JSON files, directly fixing the PBS/FUSE backup-hang class.

### 8.2 Registry pilot schema (lands with §7.1 model-metadata)

```sql
-- one row per model; the §7.1 metadata record, now relational
CREATE TABLE model (
  id            TEXT PRIMARY KEY,          -- registry model id
  source_repo   TEXT,                      -- hf repo
  revision      TEXT,                      -- resolved commit sha (§7.1c update detection)
  path          TEXT,                      -- entry point (shard-1 for sharded)
  preferred_runner TEXT,                   -- key into RUNNER_IMAGES (§7.1b)
  mmproj        TEXT,                      -- projector path, nullable
  architecture  TEXT,                      -- for FAMILY_DEFAULTS keying (§7.1a)
  context_length INTEGER,
  mtp           INTEGER,                   -- capability flag (§7.1a), nullable tri-state
  jinja         INTEGER,                   -- capability flag (§7.1a), nullable tri-state
  -- model.defaults fields folded onto the row:
  profile       TEXT, extra_args TEXT, n_gpu_layers INTEGER, chat_template TEXT,
  sha256        TEXT, pulled_at TEXT, created_at TEXT, updated_at TEXT,
  extra         TEXT                        -- JSON escape hatch for forward-compat
);
CREATE TABLE model_file (                   -- the §7.1c file-SET abstraction
  model_id TEXT REFERENCES model(id) ON DELETE CASCADE,
  rel TEXT, dest TEXT, size_bytes INTEGER, sha256 TEXT, lfs INTEGER,
  role TEXT,                                -- model|shard|mmproj|tokenizer|config
  shard_index INTEGER,                      -- ordered; shard_index=1 is the entry point
  PRIMARY KEY (model_id, rel)
);
CREATE TABLE model_backend (                -- queryable, replaces JSON list
  model_id TEXT REFERENCES model(id) ON DELETE CASCADE,
  backend TEXT,                             -- rocm|vulkan|flm|kokoro|comfyui
  PRIMARY KEY (model_id, backend)
);
```

This single schema serves **all three** of §7.1a/b/c (flags, runner, pulling) — which is the whole
reason the registry is the right pilot.

### 8.3 Cutover approach (low blast radius)

1. Build `src/hal0/db/` (connection + migrate + `db/migrations/001_registry.sql`).
2. Implement `SqliteModelRegistry` **behind the existing `ModelRegistry` interface** — callers don't
   change; only the storage backend swaps. This is the key to keeping the blast radius small.
3. **One-shot import** on first boot: read `/var/lib/hal0/registry/*.toml` → populate SQLite
   (idempotent; re-runnable). Keep `hal0 registry export` to dump SQLite → TOML for inspection/debug.
4. Cut reads/writes over to SQLite; TOML becomes a read-only export artifact, not a source of truth.
5. Ship §7.1's new fields (`revision`, `files[]`, `shards[]`, `preferred_runner`, `mtp`, `jinja`)
   **as columns from day one** — pilot + §7.1 land together, so nothing is written twice.

### 8.4 Runtime state (Phase 3, deferred — table list only)

Migrate alongside the `slots/manager` decomposition (§Phase 3): `slot_state` (replaces per-slot
`state.json`), `pull_job` (replaces the asyncio-tracked job dicts), `event`/`journal`,
`provision_checkpoint`, `decision_log`, `approval`. Each swap deletes a scattered-JSON + hand-rolled
lock/atomicity site. Do these one table at a time, not big-bang.

---

## 9. Execution methodology (ways of working)

Full standard: **`/home/mint/hal0-rework-ways-of-working.md`** — every session/agent follows it.
Summary:

- **Git:** one integration branch `rework/descar`; short-lived topic branches; small PRs;
  conventional commits with the `Co-Authored-By: Claude` trailer; never push to the `hal0`/`hal0lxc`
  deploy remote; every PR keeps `check-sunset` green and ratchets the scar baseline down.
- **Worktrees:** default *don't*; use `isolation:"worktree"` only for parallel file-mutating agents;
  one task = one worktree = pruned on completion. **Never accumulate** (the 45-worktree scar).
- **Agent teams:** this **Opus session orchestrates + reviews**; spawn **Sonnet agents** for bulk
  implementation/mechanical refactors/tests (`model:"sonnet"`), **Opus agents** for hard
  verification; parallelize independent work; **verify adversarially** for correctness-critical
  changes; continue via `SendMessage`. `minimax-swarm` for cheap high-volume grunt work.
- **Workflows:** use the Workflow tool for deterministic fan-out (find→verify pipelines, per-site
  migrations with worktree isolation) — only when explicitly opted in.
- **`/caveman`:** terse mode **for the *doing*** (implementer prompts, orchestration chatter, bulk
  passes); **full fidelity for the *deciding & reviewing*** (design, security, findings, this plan).
  Never let it degrade code quality or finding precision.
- **DoD per PR:** compiles + types + tests + CI green · scar baseline same-or-lower · deferrals carry
  a `HAL0-SUNSET:` marker · touched-area docs reconciled to code · verified by exercise/adversarial
  check · branch merged, worktree pruned · **tracker row flipped + changelog appended (§10)**.

## 10. Progress tracking

Single source of truth: **`/home/mint/hal0-rework-tracker.md`** — phase/workstream board with stable
task IDs (`P1-honcho`, `ML-1`, …), a live "in-flight" board, metrics (scar count, LOC, CI), and an
append-only changelog. **The orchestrator (Opus) session owns the file**; agents report status in
their return message and the orchestrator writes it (single-writer → no merge conflicts). Reference
task IDs in branches/commits/PRs. Optional GitHub Issues/Project mirror if parallel sessions grow
(decide before Phase 2 fans out).

---

## 11. Product reworks — round 2 (slot identity, ports, profile org)

New requirements captured 2026-07-17. These land in Phase 3 alongside the SQLite migration.

### 11.1 Slots created equal + ID-keyed identity
**Problem:** slots are keyed by **name**, which "causes issues" — renaming breaks every reference
(state.json path `/var/lib/hal0/slots/<name>/`, routing, capabilities.toml, unit name
`hal0-slot@<name>.service`, UI). Same failure class as the model id-rename problem (§7.1e). Slot
types are also NOT uniform: llm/flm/tts/img/npu-trio take different create/edit/delete paths (the
NPU-trio "shadow slots" are a whole parallel lifecycle, per the control-plane review).
**Target:**
- Every slot gets a **stable opaque/numeric `id`** as the primary key; **`name` becomes a mutable
  label** (display only). `id` threads through the SQLite `slot` table, state, routing, unit
  naming (`hal0-slot@<id>`), and UI. Rename touches only the label — zero broken references.
- **One uniform lifecycle** for all slot types: identical `create/run/edit/delete` surface
  regardless of runtime family. Fold the NPU-trio shadow lifecycle into the same path (§Phase 3
  slots decomposition). "All slots created equal."
- Delete is complete + safe (frees ports, removes unit, cleans state) — mirrors the model-delete
  hygiene work in §7.1e.

### 11.2 Robust port management (single authority)
**Problem:** slot ports are ad-hoc per-slot TOML fields with no central allocator → overlap
risk, no block-on-in-use, no free-on-delete. **Harvest note:** the `feat/brain-tool-use-hardening`
branch already built a **"global port-claim registry — one authority for who owns which port"** —
harvest it as the seed.
**Target:** a single **PortAuthority** (SQLite `port_claim` table): allocates from a configured
range, **reserves** a port to a slot id, **rejects** double-claims, **blocks** reuse while a claim
is live (and while the container holds the port), and **frees** on slot delete/unload. All slot
creation routes through it instead of hand-set port fields. Reconciles against actually-listening
ports on startup.

### 11.3 Profile organization + central source
**Problem:** seed profiles are a flat hardcoded list in `config/schema.py` (§7.1a already moves
them to shipped TOML). Beyond that, they need **organization + shareability**.
**Target (extends §7.1a / P3-schema):**
- Profiles gain **folders/tags** for organization and are **deletable** (user profiles vs shipped
  seeds distinguished).
- A **central git-backed profile source**: profiles pullable from a git folder/repo (a
  profile "registry") so tuned profiles can be shared/updated out-of-band from a hal0 release —
  analogous to the model registry. Shipped seeds live in `share/`; user/pulled profiles overlay.

**Category-C branch dispositions (2026-07-17):** KEEP/harvest `upstream-controls`,
`gpu-coexistence`, `dashboard-redesign`. DROP `turnstone-integration`.

---

## 12. Deployment / rollout constraint

**Do NOT replace the live `hal0` install on lxc105 in place.** When the reworked version is ready
to deploy:
- **Stand up a NEW LXC named `halo`** (letter "o", not the zero in `hal0`) for the reworked build.
- Run `halo` **side-by-side** with the existing `hal0` (lxc105) so the old box stays live as a
  rollback and reference during validation.
- Migrate data (Hindsight memory, model registry/store, config) into `halo`, validate end-to-end,
  then cut over on your schedule. Decommission the old `hal0` only after `halo` is proven.

Rationale: the rework is large (SQLite migration, store re-layout, perms/runtime changes); a fresh
LXC gives a clean, known-good target and a zero-risk rollback instead of mutating the running box.

---

## 13. Observability & metrics baseline (ships in every box)

**Constraint (2026-07-17): must work on shipped `halo` boxes for other users → self-contained,
ZERO external infra required.** SQLite + in-process aggregation + a native dashboard are the shipped
core; Prometheus/Langfuse/OTLP are optional bring-your-own exports, never dependencies. Today's
metrics are scattered (`_scrape_llama_metrics`, tps/ttft deques in routes, a separate bench system);
the base unifies them behind ONE measurement seam.

### 13.1 Architecture — SQLite core + bundled opt-in Prometheus/Grafana
- **Always-on core (zero-config, zero-dep):** request seam (§7.6) → **SQLite metrics tables (§7.5)**
  → in-process aggregator. Owns **per-request (T1) + bench (T3)** — Prometheus is a TSDB, wrong shape
  for high-cardinality per-request rows. A **minimal built-in "Performance" summary** (top-line
  tps/ttft + regression-vs-baseline per model, reads SQLite) is always present, even with the stack off.
- **Bundled opt-in observability stack:** Prometheus + Grafana as **companion containers** (same
  systemd+podman mechanism as OpenWebUI/ComfyUI), toggled at install (`--with-observability` / setup
  switch); **off = SQLite core only.** Prometheus scrapes hal0's `/metrics` (T2 slot gauges +
  aggregate counters, **retention/size-capped**); **Grafana ships PRE-PROVISIONED hal0 dashboards +
  datasource** (Grafana provisioning JSON) → open Grafana, see panels, zero manual setup. Ports via
  the §11.2 port-authority, LAN-bound; images pinned in the runner/manifest registry (§7.1b);
  updated through the same companion-service lifecycle.
- **Other exports (off by default):** Langfuse tracing + OTLP (dev/power users).
- **Privacy:** 100% local, **no phone-home**; retention-bounded; master on/off toggle. Any future
  aggregate opt-in telemetry to the hal0 project must be explicit opt-in (out of scope for v1).

### 13.2 What's measured — 3 tiers
- **T1 per-request (core):** TTFT, prefill-TPS + decode-TPS (separately), queue/wait, prompt/completion
  tokens, ctx used, prompt-cache/KV-reuse hit, spec/MTP accept-rate, stop reason, ok/error, correlation
  id, slot/model/runner/device/modality/client. *Source of truth: llama.cpp `timings` (exact), FLM/
  comfyui equivalents — normalized, never estimated.*
- **T2 per-slot (timeseries):** VRAM + **GTT** bytes (Strix Halo unified memory), RAM, GPU/NPU util,
  power, temp, in-flight, KV occupancy; lifecycle events + **cold→warm load time** + GPU-arbiter waits.
- **T3 quality/bench:** tps/ttft per (model × runner × profile × hardware) baseline; **MTP draft-accept
  rate**; tool-call success; error/refusal rate.
- **hal0 differentiators to first-class:** MTP accept-rate, GTT/VRAM pressure, TTFT-cold-vs-warm,
  arbiter contention, NPU-trio per-role latency, **tokens/sec/watt** (the meaningful local "cost").

### 13.3 Schema (SQLite, §7.5 runtime DB)
```sql
request_metric(id, ts, request_id, slot_id, model_id, runner, device, modality,
  prompt_tokens, completion_tokens, ctx_used, ttft_ms, prefill_tps, decode_tps,
  queue_ms, total_ms, cache_hit, spec_accept_rate, stop_reason, ok, error_code, client)
slot_sample(ts, slot_id, state, vram_bytes, gtt_bytes, ram_bytes, gpu_util, npu_util,
  power_w, temp_c, inflight, kv_used)
slot_event(ts, slot_id, event, from_state, to_state, duration_ms, reason)
bench_run(id, ts, model_id, runner, profile, hw_hash, tps, ttft_ms, spec_accept, quality, baseline)
metric_rollup(bucket, dim, ...)   -- hourly/daily aggregates for long retention
```

### 13.4 Baseline & regression
Bench each model once **on install/first-load** → store per-(model×hardware) baseline → surface
**regressions** in the dashboard ("qwen3 was 45 tps, now 30"). Reuses the existing bench harness,
unified onto the same schema.

### 13.5 Shipped-box constraints
- **Bounded storage:** raw `request_metric` rows retained N days (configurable), downsample
  `slot_sample`, keep `metric_rollup` long. Auto-prune. Never fill a user's disk.
- **Low overhead:** async writes off the inference hot path; flag-gated; near-zero when off.
- **Hardware-graceful sampling:** VRAM/GTT/NPU/power via `hardware/probe`; tolerate missing sensors
  (NVIDIA/AMD/Intel/CPU-only) — degrade, never crash.
- **Zero-config:** works out of the box; native dashboard, no Grafana/Langfuse needed.

### 13.6 Principles
One measurement seam (never re-scatter) · async/non-blocking · Prometheus label cardinality =
slot/model/runner/device/modality only (per-request detail → SQLite) · correlation id threads
request→slot→dispatch→(optional Langfuse) · retention + rollup from day one.

### 13.7 Sequencing
Lands **after ML-1 (SQLite pilot)** — reuses those tables and the §7.6 request seam. Native
dashboard view extends the existing Benchmarks pane. Optional exports last.

---

## 14. Kanban board — ownership inversion + security hardening

Verified on rework/descar. Today hal0 is a thin audited **proxy** in front of a **Hermes-owned**
board (`HermesKanbanClient` → loopback `127.0.0.1:9119/api/plugins/kanban`, `board/__init__.py:96`).
Elegant on paper; in practice it makes an *optional* agent a hard SPOF, leaves the surface
unauthenticated, and hands a promptable LLM immediate write access.

### 14.1 🔴 SECURITY — fast-track (do ahead of the full board rework)
- **Board surface is unauthenticated → LAN-RCE.** The chat proxy gates every call
  (`chat_proxy.py:412/530` origin-allowlist + HMAC cookie + `require_browser_auth`); the **board
  routes apply none** — `board.py:363` bare `websocket.accept()`, all REST + `/chat` have no auth.
  `POST /api/board/chat` surfaces the full hal0-admin MCP catalog incl. `AUTONOMOUS_WRITE_TOOLS`
  (`admin.py:318`: slot_load/unload/edit, model_swap/assign/edit, settings_reload, memory_*) that
  run **without approval**. On `0.0.0.0:8080`-no-auth this = anyone on the LAN drives the box.
  **FIX (small, mechanical, Opus-owned):** apply `agents/_auth.py` (`check_ws_origin_and_cookie` +
  `require_browser_auth`) to every board route + `/chat` + the events WS. *(lxc105 stays exposed
  until deploy — optional live hotfix.)*
- **Default `brain_chat.read_only = True`** (`config/schema.py:~2989`) and/or route board mutations
  through the `ApprovalQueue` — closes injection→autonomous-mutation (a malicious task body the
  brain reads can emit tool calls, incl. via the text-tool-call scraper `board_chat.py:976`).
- **Scope the brain to a session-pinned board/tenant** — today `?board=<slug>` is threaded verbatim
  (`board.py:60`), no authz; brain can enumerate/switch/mutate any tenant's board.

### 14.2 Resilience (Hermes is a SPOF today)
- Drop the **120 s** board read timeout (`board/__init__.py:125`) to ~10–15 s; add a **read-through
  cache** of last-good `GET /board` served `stale` on `BoardUnreachable` so the UI degrades to
  read-only instead of going dark. Close the client on shutdown (`aclose()` is dead in prod —
  `api/__init__.py:1265` teardown omits `hermes_kanban`; fd leak). Add a token-resolution health
  probe (the HTML-scrape auth `board/__init__.py:175` breaks silently on any Hermes template change).

### 14.3 Ownership inversion (the real fix — Phase 3, with SQLite)
**hal0 OWNS the board in its SQLite runtime DB (§7.5); Hermes/agent = optional executor behind ONE
narrow sync seam.**
- Tasks/columns/links/orchestration live in hal0 SQLite → board survives Hermes uninstall/reprovision
  (today it does not); brain reads/writes the *local* store directly (no loopback, no token scrape,
  no 120 s hang, no SPOF); inherits hal0 auth + `ApprovalQueue` + `ToolPolicy`; two-writer races
  solved by one transactional store with **row versions/ETags** (today: last-write-wins, no protection).
- **Hermes reduced to one seam:** hal0 pushes "ready/dispatch" tasks + ingests worker status/run
  events (`/dispatch`, `workers/active`, `runs/{id}`, events-WS subset). Everything else becomes
  hal0-native → deletes most of `board.py`'s passthrough and BOTH parallel tool tables
  (`board.py` handlers vs `board_chat.py:_resolve_tool` vs `_READS` — three hand-maintained copies
  of one upstream contract, `finding 9`).
- **Replaceable executor:** with hal0 owning the board, the dispatch/worker backend (Hermes today)
  becomes swappable — this is the hinge for the **Turnstone** evaluation (§ pending): the question
  reduces to "is Turnstone a better *executor* behind the narrow seam than Hermes?", not "who owns
  the board."

Ties to: §7.3 (brain first-class), §7.4 (Hermes narrow), §7.5 (SQLite), §11.1 (slot/board ID keying).

### 14.4 Turnstone verdict (evaluated 2026-07-17)
**Rejected for the brain/kanban-worker role.** Turnstone (turnstonelabs/turnstone, Apache-2.0,
young but active) is a purpose-built tool-agent orchestrator with a Redis-backed queue worker mode —
conceptually a fine *executor*, but: it has **no board** (can't replace the kanban), its hal0
integration **coexists with Hermes** (a 3rd runtime + JWT + 2nd SQLite + unverified upstream schemas
= net-additive coupling), its worker mode isn't wired and needs **Redis** (against single-node
SQLite), and the brain is **already hal0-native** (in-process `board_chat`) so Turnstone would be a
regression there. **Path instead:** hal0-owned SQLite board (§14.3) + reuse the in-process
`board_chat` tool-loop as the optional card executor → drops Hermes from this role with zero new
runtimes. Turnstone parked as a *possible future opt-in heavy executor only*, after schema verification.

**Deletion note:** Turnstone is **already merged into main** as a third bundled agent
(`src/hal0/agents/turnstone/` + `turnstone_provision.py`, coexists-with-hermes). Per "keep only
Hermes" it joins pi_coder/opencode in the Phase-1 speculative-agent deletion (add to P1-agents/batch 2).

---

## 15. Hermes plugin — standalone repo + native update path

**Refines §7.4.** Instead of vendoring the hal0 Hermes plugins inside the hal0 monorepo (today: 3
copies — `installer/agents/hermes/plugins/hal0-memory/` and
`src/hal0/agents/hermes/plugins/memory_hindsight/` byte-identical + a TS reimpl), extract them to a
**separate git repo that Hermes installs and updates via its own plugin mechanism**.

### 15.1 Why
- **Kills the 3-copy dedup** — ONE source of truth in the plugin repo; hal0 stops vendoring +
  re-provisioning it. The self-contradicting "this is the only copy now" docstring goes away.
- **Decoupled release cadence** — a plugin bugfix ships from the plugin repo without a full hal0
  core release; Hermes pulls it (auto-update or a dashboard "update plugin" button).
- **Dashboard-manageable** — Hermes's native plugin UI (already proxied by hal0's plugin host,
  `api/plugins/`) surfaces install/update/version, so users update it in-dash.
- **Easier changes** — plugin iteration is independent of hal0's CI/release machinery.

### 15.2 What moves
The hal0↔Hermes glue plugins only (NOT the kanban — that goes hal0-native, §14): the **hal0-memory
(Hindsight)** plugin and the **hal0-provider** (AI-provider wiring) plugin. Proposed:
**`Hal0ai/hal0-hermes-plugins`** — one repo, per-plugin subdirs (`hal0-memory/`, `hal0-provider/`),
one release/tag stream. (Single mono-repo > N repos for maintenance; revisit only if a plugin needs
a wildly different cadence.)

### 15.3 How it's wired
- **Install:** Hermes installs the plugin from the repo **pinned to a tag/sha** (not floating main) —
  hal0's provisioner points Hermes at the pinned version instead of copying files in. Supply-chain:
  pin + verify.
- **Update:** Hermes auto-update or dashboard button pulls a newer pinned version; hal0 core untouched.
- **Offline / air-gap fallback:** hal0 **bundles a pinned copy** of the plugin as a fallback so fresh
  or air-gapped installs work with zero network; the repo is the canonical source + online update
  path, the bundle is the floor. (One copy generated from the repo at hal0 build time — not
  hand-maintained — so no drift.)
- **Compatibility contract:** the plugin talks to hal0's memory-MCP + provider API — version the
  contract (semver) and have the plugin assert a min hal0 API version, so a plugin update can't
  silently break against an older hal0. We own both sides now, so this is controllable (unlike the
  Hermes-upstream pin we're *removing* in §Phase 4).

### 15.4 Scope + surface impacts
- **New repo `Hal0ai/hal0-hermes-plugins`** — expands work scope beyond `Hal0ai/hal0` (authorized
  2026-07-18 for this purpose). Needs its own CI/release (lightweight) + a version-pin reference in
  hal0.
- **Installer:** provision Hermes to install the plugin from the pinned repo version (+ bundled
  fallback), not by copying vendored files. — *§7.2/§7.4*
- **UI:** Hermes-dashboard plugin surface shows the hal0 plugin's version + update control (via the
  existing plugin host proxy). — *P3-hermes-mem*
- **Docs:** plugin repo README + the hal0↔plugin compatibility/version doc.

### 15.5 Revision — monorepo + published package (supersedes the separate-repo default)
**Decision (2026-07-18): keep the Hermes plugins IN the hal0 monorepo, published as independent
packages** — do NOT split to a separate `Hal0ai/hal0-hermes-plugins` repo (§15.2 superseded).
Rationale: the plugin's Hermes-native update path depends on it being an **independently-versioned
published artifact** (PyPI for `hal0-memory`, npm for TS), NOT on repo location; a monorepo *also*
gives **atomic contract changes** (core memory-MCP/provider API + plugin in one commit/PR) — the
biggest recurring win for a solo maintainer, vs coordinated 2-repo PRs. Consequences:
- Plugins live under a clear package boundary in the hal0 repo (e.g. `plugins/hal0-memory/`,
  `plugins/hal0-provider/`), each its own `pyproject`/`package.json`, **independently published +
  versioned** in hal0 CI. hal0 core pins the compatible version; Hermes installs/updates by package
  version (in-dash). Bundled offline fallback still generated from the same package (no drift).
- **Work scope stays `Hal0ai/hal0` only** — no new repo.
- **VERIFY FIRST (HP-0):** how Hermes actually consumes plugins (package/tarball/manifest vs
  git-clone-only). Package-install → this monorepo plan holds. Git-clone-only → fall back to a small
  separate plugin repo (§15.2) OR publish a package Hermes can point at. Decide after that check.

### 15.6 HP-0 RESOLVED — Hermes supports pip-package plugins → monorepo confirmed
Verified against upstream docs (hermes-agent.nousresearch.com/docs/user-guide/features/plugins):
Hermes installs plugins from **either** a **git repo** (`hermes plugins install user/repo`) **OR a
pip package** declaring `[project.entry-points."hermes_agent.plugins"]`, and updates via
`hermes plugins update <name>`. Because `hal0-memory` is a Python plugin (register()/schemas.py/
tools.py/plugin.yaml), the **pip-package path lets us keep the monorepo** (§15.5 holds) — publish
`hal0-memory` to PyPI from the hal0 repo with the `hermes_agent.plugins` entry point; Hermes installs
+ updates it natively. **No separate repo.** This also fixes the real bug: today hal0 `_copy_plugin_tree`s
files into `$HERMES_HOME/plugins/` so Hermes has no source record and can't update the plugin —
switching to a pip-installed, entry-point plugin makes `hermes plugins update` work. (Git-repo path
was the fallback only if Hermes were git-only; it isn't.) The TS `hal0-provider` variant, if kept,
uses the git/web path — but the memory plugin, the one that matters, goes pip.

### 15.7 Plugin naming + granularity
- **`hal0`** = the provider plugin (points Hermes at hal0 `/v1`, **slot/model auto-detection**, tools).
- **`hal0-memory`** = the Hindsight memory plugin (`kind: exclusive` MemoryProvider).
- **Keep them as TWO logical plugins, not one merged plugin** — they hook different Hermes extension
  points (a provider vs a `kind: exclusive` MemoryProvider); one `plugin.yaml` can't be both, and
  separation keeps memory independently enable-able (hal0 inference without hal0 memory, or vice versa).
- **Distribute as ONE `hal0` pip package** exposing both via `[project.entry-points."hermes_agent.plugins"]`
  → single `hermes plugins install hal0` / `hermes plugins update hal0`, one version, but two extension
  points. **VERIFY (HP-1):** Hermes loads 2 entry-point plugins from 1 package; if it's one-plugin-
  per-package, ship two packages (`hal0`, `hal0-memory`) with the same names.

---

## 16. Hermes Python library (`AIAgent`) for the brain — evaluated, rejected for core

Considered embedding the Hermes Python library (`from run_agent import AIAgent`) to power hal0-brain
(docs: hermes-agent.nousresearch.com/docs/guides/python-library). **Rejected for the core brain.**
The library imports the **full Hermes runtime**; it takes a `base_url` (can target hal0 `/v1`),
toolsets, `skip_memory`, but: **no streaming**, **no tool-call approval/gating**, and it's
**thread-unsafe (one instance per request)**. Against the rework goals it fails three ways:
1. **Re-couples the core to Hermes** — today `board_chat` is hal0-native with ZERO Hermes dep;
   importing `AIAgent` makes the Hermes lib a hard dependency of the core dashboard chat. Opposite of
   §7.3 (brain first-class, decoupled) and deepens the version-pin §Phase 4 is shedding.
2. **Loses SSE streaming** — the brain streams token/thinking/tool_call/approval frames; `chat()`
   returns final text only.
3. **Loses approval-gating** — the loop is internal to `AIAgent`, so no mid-loop pause for the
   `ApprovalQueue` that guards slot/model/settings/memory writes.
The brain's loop isn't the hard part — §7.6's small hal0-owned `toolloop/engine.py` is streaming +
approval-aware + hal0-local (`/v1` + hal0-admin MCP) + zero-dep, strictly better here.
**Where it may fit:** the optional heavy background-worker executor behind the §14.3 narrow seam
(long multi-step card runs) — off the brain hot path. Even there, hal0-native is preferred and an
external agent (Hermes lib / Turnstone) stays opt-in (consistent with §14.4).

### 15.8 Hermes built-in plugins — what to reuse / drop
Verified (docs: .../features/built-in-plugins). Built-ins are all opt-in/disabled by default.
- **Memory:** NO built-in (`plugins/memory/` excluded from bundled scan; set up via
  `hermes memory setup`) → **keep custom `hal0-memory`** (Hindsight). Confirmed.
- **Model provider:** NO plugin — Hermes configures the LLM backend in **core** (OpenAI-compat
  `base_url`). ⇒ **The `hal0` provider / "slot-model auto-detect" is NOT a plugin — it's config:**
  point Hermes at hal0 `/v1` (lists slots via `/v1/models`, filter `owned_by==hal0`). **Drop the
  provider plugin.** So the ONLY shipped plugin is `hal0-memory`. *(VERIFY: Hermes model config
  handles base_url + a dynamic/filterable model set for hal0's slots.)* Simplifies HP-1.
- **Kanban:** the built-in `kanban/dashboard` IS the Hermes-resident board hal0 uses today — the very
  thing §14 inverts to hal0-owned. **Do not build on the built-in kanban.**
- **Reuse `observability/langfuse`** (opt-in): route the Hermes agent's own traces to hal0's
  self-hosted Langfuse via `HERMES_LANGFUSE_BASE_URL` — complements §13's optional Langfuse export.
- Ignore consumer built-ins (spotify/google_meet/teams/achievements/image_gen) — keep disabled.

### 15.9 hal0 as a first-class Hermes Model Provider Plugin (supersedes §15.8 "provider=config")
Verified (docs: developer-guide/model-provider-plugin, guides/build-a-hermes-plugin,
user-guide/configuring-models): Hermes has a **custom Model Provider Plugin** extension point — every
built-in provider (openrouter/anthropic/nvidia/deepseek/…) is one; third parties drop a dir under
`$HERMES_HOME/plugins/model-providers/<name>/`, call `register_provider()` → auto-wired into
`PROVIDER_REGISTRY`/`auth.py`. **So ship a real `hal0` provider plugin, not base_url config.**
Unlocks (vs clumsy pointing):
- **First-class named provider**: `--provider hal0`, config.yaml, dashboard Model Settings dropdown,
  `doctor`, setup wizard.
- **All 11 auxiliary slots** run on local hal0 models (vision, context-compression, web-summary,
  **approval-scoring, MCP tool-routing**, session-title, skill-search) → Hermes runs **fully local,
  main + side-jobs, zero cloud**. This is the real integration win.
- **`ProviderProfile` hooks** `prepare_messages()` / `build_extra_body()` (ctx: model, base_url,
  reasoning_config, provider_preferences) → inject hal0 slot routing, MTP/thinking/vision handling,
  `owned_by==hal0` filtering.
- Native credential/auth wiring via `auth.py`.
⇒ **hal0 ships TWO plugins**: `hal0` (model-provider) + `hal0-memory` (memory-provider; uses the
`pre_llm_call` hook to inject Hindsight context). Both first-class provider-plugin kinds.
**VERIFY:** do model-provider plugins support the pip entry-point path, or directory-drop only?
(Directory-drop → hal0 provisions the dir from the monorepo source; pip → published package.)

### 15.10 Hermes dashboard extension — optional hal0 panels (post-core)
Verified (docs: features/extending-the-dashboard). Extension = additive UI only: 11 shell slots
(header/sidebar/footer/overlay/pre-main/post-main) + page `:top/:bottom` pairs + new tabs + tab.override;
IIFE JS bundle, React via `window.__HERMES_PLUGIN_SDK__`, shadcn/ui; a plugin **Python backend route**
(`/api/plugins/hal0/*`) can proxy to hal0-api.
- **Routing targets are NOT here** — no model-routing slot exists; that's the §15.9 provider plugin
  (hal0 slots appear natively in Model Settings + aux pickers). Don't override /config for it.
- **Optional killer panels (opt-in, POST-core, keep to 1–3):** (a) "hal0 Control" tab
  (slot/model/GPU load-unload-swap inside Hermes); (b) live telemetry widget in `header-right`
  (tps/ttft/MTP-accept/GTT, ties §13); (c) aux-routing status banner (`header-banner`, warm/cold +
  offline warning); (d) Hindsight memory inspector; (e) model catalog + pull.
- **Guardrails:** it's a 2nd UI surface alongside hal0's own dashboard — small set only, not on the
  de-scar critical path. Security: Hermes dashboard bypasses session auth (localhost-bind, no plugin
  sandbox) — keep LAN-safe (ties KB-1).

### 15.11 hal0 dashboard theme (Hermes)
Ship a **hal0-branded theme** for the Hermes dashboard — `customCSS` (32 KiB/theme cap) + theme vars
(`--color-*`, `--radius`, `--theme-asset-*`) via a plugin manifest. Opt-in, cosmetic, low-risk;
bundles with HP-4. (Post-core polish.)

## 16.1 Brain engine fork — Hermes API server reopens it (revises §16)
The Hermes **API server** (features/api-server) supports **streaming** (`GET /v1/runs/{id}/events`)
and **approval gating** (`POST /v1/runs/{id}/approval`) — the two things that made §16 reject the
Hermes *Python library* for the brain. So proxying the brain to the Hermes API server is now viable.
Fork:
- **A (current §7.3):** hal0-native in-process brain, ZERO Hermes dep — the box's steward works
  standalone even if Hermes is down/uninstalled. hal0 keeps a small tool-loop.
- **B:** brain proxies to Hermes `/v1/runs` → deletes hal0's tool-loop + omni_router (one runtime),
  leverages deep Hermes integration — but brain HARD-REQUIRES Hermes running (+ API_SERVER_KEY).
- **Crux:** the brain is the box's own control surface (slots/models/settings); depending on an
  optional bundled agent to control the box is fragile. B only makes sense if Hermes becomes
  REQUIRED/always-on.
- **RECOMMENDATION: hybrid** — brain stays hal0-native (A) as the resilient always-works core; the
  Hermes API server is an **optional escalation target** for heavy multi-step agent work (same
  optional-executor-behind-a-narrow-seam pattern as §14.3/§14.4). _(Decision pending.)_

**DECISION (2026-07-18): Hybrid + Hermes optional.** Brain stays hal0-native/in-process (§7.3) — the
resilient always-works box-control surface. Hermes is an **optional bundled agent** (installable/
removable), so NO core feature (brain, board §14, control) may hard-depend on it. The Hermes API
server is an **optional escalation target** for heavy multi-step agent tasks, behind a narrow seam
(consistent with §14.3/§14.4). The provider/memory/dashboard plugins (§15) are for Hermes-when-present,
never a core dependency. This is a standing constraint: **core works without Hermes.**

---

## 17. Installer / setup overhaul (Lane E)

Verified current-state (agent-mapped). The apply **core** is sound — `install/orchestrate.py::apply_setup`
is a single provisioning algorithm all 4 apply paths (install.sh `--auto`, interactive `hal0 setup`,
live-API `/api/install/apply-selections`, dashboard FirstRun) funnel into, and `network.py`/`Selections`
are clean. The rot is around it:
1. **No single authority spanning shell↔Python** — `install.sh` (2385 lines) does half the provisioning
   imperatively (users/dirs/perms/units/seeds + 4 inline subsystems + NPU .deb + iptables/apparmor
   shims), delegates the rest to `hal0 setup`; they share only hand-mirrored constants.
2. **Profile/model derivation duplicated 6×** with divergent MTP policy: `install/profile_derive.derive_profile`
   (live), `capabilities/profile_fit.profile_name_for_fit`, `slots/manager._base_profile_for_backend`,
   `hardware/recommend.recommend_primary_slot` (**fully orphaned/dead**), + 2 model-pickers
   (`recommend._pick_chat_model` dead vs `install/suggest.suggest_models` live) + 2 budget fns.
3. **Ownership described but not enforced:** `install/perms.py` is a good declarative `OwnershipStore`
   (`ownership_table`/`plan`/`commit`) but **dead in the apply path** (doctor-only, root-only); real
   chowns are scattered imperatively across install.sh in 3 conflicting regimes. Hardened-flip = dead code.
4. **Empty `src/hal0/installer/` stub** vs real code in `install/` + `cli/setup_*` (navigation trap).
5. **Slot roster hand-mirrored ×4** (install.sh loop, `static_seeds`, `setup_command._SETUP_SLOTS`,
   `installer._SLOT_META`) with ports hand-assigned.
6. **Re-run is expensive/partly unsafe** — no converged fast-path; rebuilds venvs/images/npm each run.
7. **Two first-run UIs, different model policy** — CLI pick-free scaffolds vs dashboard tier bundles.
8. `setup_ui.py` (1015) — bespoke termios TUI with every widget doubled (raw-tty + numbered).

**Overhaul (mostly consolidation + deletion, not new machinery):**
- **Thin shell + thick Python:** shrink `install.sh` to ~200-line bootstrap (verify + python≥3.12 + venv
  + podman; keep `preflight.sh`), hand everything else to a Python provisioner in the now-empty
  `installer/` package: `hal0 provision --stage=system|services` + `hal0 setup`.
- **One profile authority:** fold the 6 into `derive_profile(mtp: bool)`; **delete `hardware/recommend.py`**;
  one model-picker (`suggest`), one budget fn; `SEED_PROFILES` the one catalog (delete `installer/etc-hal0/profiles.toml` + its prune dance).
- **Enforce `perms.py`** as the single ownership authority (run `plan/commit` from `provision --stage=system`);
  delete hardened-flip; **one `hal0`-user model**, hal0-api `User=hal0` (§7.2); **Quadlet `.container`**
  units → kills imperative `podman run` ExecStart + the FORWARD/apparmor shims.
- **One slot roster** in Python (kills ×4 mirror); converged fast-path re-runs (optional subsystems as
  `is_installed()/ensure()` plugins).
- **Pick-free default everywhere** (retire dashboard tier→models as default); **SQLite first-run gating**
  (§7.5) instead of the `models-dir-empty AND no-sentinel` fs heuristic.
- **Minimal first-run wizard** (drop the doubled TUI → one `rich`/`questionary` path; drop per-capability
  model pickers). **Remove** the Honcho block (~250 lines) + turnstone + pi_coder; **OpenWebUI stays**
  (→ Quadlet unit). Removes ~400+ lines from install.sh.
Ties §7.2 (perms/quadlet), §7.5 (SQLite), §11.3 (profiles). Full map in the analysis output.

---

## 18. Hermes plugin suite — corrected + EXPANDED (supersedes §15.5/15.6/15.9 packaging)

Verified against all 4 dev docs. **KEY CORRECTIONS to earlier sections:**
- **NOT pip packages — uniform DIR-DROP + config-apply.** Memory is **dir-drop ONLY** (no pip
  entry-point group exists for it). So the §15.5/15.6/15.9 "one pip package with entry points"
  conclusion was **wrong** for memory. Keep hal0's existing `_copy_plugin_tree` → `$HERMES_HOME/plugins/`
  path (already works for hal0-memory). Uniform dir-drop is the coherent story; don't pip-package.
- **Memory injection = `prefetch()` + `system_prompt_block()` + `sync_turn()`** (base
  `agent.memory_provider.MemoryProvider`). There is **NO `pre_llm_call` hook** (corrects §15.9).
- **The `hal0` model-provider plugin was already REMOVED** (it hardcoded a dead base_url). Chat now
  works via `config.yaml` `provider: openai` + `base_url=…:8080/v1` (the Turnstone pattern). Re-adding
  a `ProviderProfile` is **OPTIONAL** — its only real value is pinning the **aux slot** local via
  `default_aux_model` (so even compression/vision/summarization LLM calls stay on hal0).

**The suite (4 surfaces, 3 different mechanisms):**

| Plugin | Hermes kind | hal0 surface | Status |
|---|---|---|---|
| **hal0-memory** | `MemoryProvider` dir-drop, `kind: exclusive` | `/api/memory/*` | ✅ built — **keep 2 parity-locked copies** (§18.1/§23.1), NOT 3→1; rename tools + add config schema |
| **hal0-image** | `ImageGenProvider`, `kind: backend`, `generate()` | `/v1/images/generations` (ComfyUI) | 🔨 new |
| **hal0-tts** | config-only **command provider** + `hal0-tts-speak` shim | `/v1/audio/speech` (kokoro/qwen3tts) | 🔨 new |
| **hal0** (optional) | `ProviderProfile`, `api_mode=chat_completions` | `/v1/chat/completions` | ⚠️ optional — aux-slot-local |

**Deepest integration = Hermes runs 100% local across chat + memory + image + tts on hal0 hardware.**
Buildable sketches (hal0-image `__init__.py`, hal0-tts config block + shim) captured in the analysis.

**Gotchas:** `config.yaml` is Hermes-owned → apply only specific keys via `hermes config set`, never
rewrite (else `image_gen.provider`/`tts.provider` clobbered on migrate). Vendored ABC stubs +
import-fallback keep it optional (**hal0 core never imports Hermes** — standing constraint holds ✓).
TTS name must be `hal0` (built-in names shadow). Image `generate()` uses a 180s timeout (img-slot
cold-start). `kind:` + dir-root differ per plugin (`plugins/`, `plugins/image_gen/`). Auth via env
+ `secrets/agents/hermes.env` (0600), never world-readable config.yaml.

---

## 19. Voice stack — TTS + STT model choices (researched mid-2026)

hal0 hardware = AMD Strix Halo, **no CUDA**. Usable runtimes: llama.cpp (ROCm/Vulkan GGUF), FLM NPU,
ONNX Runtime. Prize = anything that runs as GGUF on the existing llama-server. LLM-TTS works as an
LLM backbone emitting codec tokens (GGUF on llama.cpp) + a small codec decoder (SNAC/DAC/NeuCodec/
NanoCodec, CPU/ONNX). Only **OuteTTS** has the vocoder wired into a native llama.cpp binary.

### TTS — ranked
1. **Kokoro-82M (ONNX)** — KEEP as default. 82M, Apache-2.0, 54 voices, ~5x RT CPU, runs on existing
   ONNX path, zero new infra. Limit: no voice cloning (fixed voices).
2. **OuteTTS-1.0-1B (GGUF)** — ADD. Best "runs as a hal0 slot" pick: native `llama-tts-outetts-v1`
   binary, vocoder built in, loads on llama-server (ROCm/Vulkan). Q4_K_M 818MB. Voice cloning from
   ~10s, multilingual. Cleanest path to cloning without a PyTorch stack. Start here.
3. **NeuTTS Air (GGUF + NeuCodec + espeak)** — optional, more natural cloned voice. 748M Qwen2,
   Apache-2.0, <1GB, realtime on CPU (tested on Ryzen AI same Strix family). One extra decoder component.
- **Kani-TTS-2 (400M GGUF, lfm2)** — lightest cloning option (240MB Q4, 3GB VRAM, Feb 2026).
- **Orpheus-3B (GGUF + SNAC)** — best emotion tags (`<laugh>`), ~200ms stream, heaviest (Q8 ~4GB).
- **Chatterbox-Turbo (MIT, ROCm container)** — top open cloning quality (65% vs ElevenLabs, ~75ms) but
  a separate PyTorch-ROCm service. Reserve for a premium/expressive "assistant voice."
- Keep **qwen3tts** (already on iGPU). Note Qwen3.5-Omni is API-only — not a local upgrade.
- **Avoid:** Fish/OpenAudio S1/S2 (non-commercial license + CUDA), F5-TTS (CC-BY-NC), XTTSv2 (restrictive).

### STT — ranked  (biggest concrete win in the rework)
1. **whisper.cpp large-v3-turbo (GGUF, Vulkan)** — ADD. The ONLY major Whisper runtime with ROCm+Vulkan
   (same GGML foundation as llama.cpp) → GPU-accelerated on hal0, 99 langs, far more accurate than
   Moonshine. Highest-value STT change.
2. **Parakeet-TDT-0.6B-v3 (ONNX, `onnx-asr`, ROCm EP)** — best English WER (6.32% vs whisper 7.44%),
   ~670MB int8, no CUDA. Add if STT is English-dominant (25 EU langs only, no CJK).
3. **Moonshine** — KEEP on NPU/FLM for ultra-low-latency realtime dictation.
- **Avoid faster-whisper:** CTranslate2 has no ROCm/Vulkan → CPU-only on hal0. Kyutai/Canary = PyTorch,
  no GGUF/ROCm path.

### Integration (ties §7.1b runner registry + §13 voice slots)
- **Slot straight into llama-server (best):** OuteTTS GGUF, whisper.cpp GGUF (Vulkan).
- **llama.cpp + tiny decoder (still one box):** NeuTTS / Kani / Orpheus.
- **New ROCm container (premium only):** Chatterbox-Turbo, Parakeet-ONNX server.
- **New capability unlocked:** voice cloning (none in current GGUF path) + streaming.

### 18.1 hal0-memory vs upstream hindsight plugin (HP-1 decision)
Upstream ships `plugins/memory/hindsight` → talks to **Hindsight by Vectorize** directly (cloud
`api.hindsight.vectorize.io` or `HINDSIGHT_MODE=local_external`+`api_url`); single `bank_id` per
profile; tools `hindsight_retain`/`recall`/`reflect`; NO shared-bank concept.
**Decision: keep custom `hal0-memory`** — it hits hal0-api `/api/memory/*` front door and gives
`private:<agent>` + **shared** banks (cross-agent shared brain), hal0 auth, MCP wiring — the upstream
plugin can't. BUT **align to upstream shape**: same tool names (`hindsight_retain/recall/reflect`),
same `~/.hermes/hindsight/config.json` + `local_external` config layout → familiar UX + easy upstream
drift tracking + less bespoke surface. Ref: `/home/mint/hal0-refs/hermes-hindsight-plugin.md`.

### P2-device — CORRECTION (spec 2026-07-18, spec-p2-device.raw)
Plan's "delete 4 device↔backend translators across 43 sites / device sole truth" was PARTLY WRONG.
TWO `backend` concepts: **A** = deprecated MIRROR of device (delete) vs **B** = runtime token
`rocm|vulkan|cpu|flm`, already device-derived (KEEP — SlotCard chip, NPU dispatch, argv). Corrected scope:
- Delete only `device_to_legacy_backend` (+`_DEVICE_TO_LEGACY_BACKEND`). KEEP `canonical_device`
  (the normalizer to standardize on), `device_to_backend` (Concept B), `map_backend_to_device` (legacy read + caps v1→v2 migration).
- Real dual-write = 5 write sites (slot_config/__init__:567, orchestrator:680, stacks/apply:239,
  slot_commands:463+552) + 2 fields (`SlotConfig.backend`, `CapabilitySelection.backend`).
- ⚠ TRAP: convert both `_promote_backend_to_device` validators to read-only promote-then-drop shims,
  NOT delete — no slot-TOML migration exists; delete = legacy cpu/npu slots regress to gpu-rocm.
- API-response `backend` (Slot dataclass) = Concept B, NOT a dual-write. Leave.

### HP-1 CORRECTION (spec-plugin-suite.raw)
hal0-memory is 2 copies BY DESIGN (source `src/hal0/agents/hermes/plugins/memory_hindsight/` importable +
seed `installer/agents/hermes/plugins/hal0-memory/` hyphen), byte-identical, **parity test-locked**
(test_seed_parity.py). NOT drift, NOT 3→1. pi_coder TS reimpl already gone. Real work: (1) fix 2 stale
docstrings that falsely claim memory_hindsight deleted; (2) rename tools hal0_memory_* → upstream
`hindsight_retain/recall/reflect` (keep hal0 `shared` param); (3) add `get_config_schema`/`save_config`
(~/.hermes/hindsight/config.json local_external). Keep BOTH copies. hal0-provider = skip/flag-gate
(existing providers.custom + auxiliary.* already deliver local chat + aux).

## 20. Bench system rework (BENCH)
Bench was built OUTSIDE hal0, stitched in → buggy; doesn't adapt to HW probe (card0/card1, ROCm
device index, NPU — hardcodes single card/ROCm0). Rework to first-class, coordinated with §13 OBS
(shares the `bench_run` SQLite table + baseline-on-install + regression) + §7.1b runners + hardware/probe.
Targets: (a) HW-aware device targeting (bench a specified GPU/NPU from probe topology, per-device
results); (b) runner-registry-aware image+flags; (c) tracking→SQLite bench_run (tps prefill/decode,
ttft, mtp_accept, power, vram/gtt, baseline, regression); (d) dashboard matrix (model×runner×device×hw)
+ trends + regression, unified with §13 Performance view; (e) planner→runner→telemetry→quiesce
orchestration (preserve #1261 resume-no-dup fix); (f) fix stitched seams (raw HTTPException, /run alias,
external-origin assumptions, hardcoded device). Spec: `hal0-specs/spec-bench.raw` (DONE). Tasks BENCH-1..8. Root cause: bench discards probe topology (reads gpus[0] only), seam hardcodes -dev ROCm0, cell_key excludes device -> multi-GPU collapse. Fix: Device in identity + bench/topology.py (verified ROCm ordinal via llama-bench --list-devices).

### 20.1 Bench = auto-tuner (model-config-driven + tuning matrix)
- **Model-config-driven:** bench uses the model's §7.1 metadata (preferred_runner, ModelDefaults
  mtp/jinja/extra_args/ngl/ctx, profile) as baseline — bench "as it actually runs."
- **Tuning matrix (main feature):** sweep axes → comparison matrix. Axes: runner/backend (vulkan vs
  rocm vs cpu — the vulkan↔rocm compare), card (card0/card1), NPU; flags (mtp on/off, b/ub, ngl, ctx,
  fa, kv-quant); device. Each cell = a bench_run row (tps prefill/decode, ttft, mtp_accept, power,
  vram/gtt). Declarative TuningPlan (config variants) run via planner→runner→telemetry→quiesce, dedup/resume.
- **Recommend + write-back:** pick best config by objective (tps | tps/watt | ttft) → offer to write
  back to the model record (preferred_runner + ModelDefaults) — closes the loop with §7.1 model-owns-config.
- **Display:** matrix (rows=configs, cols=metrics) + vulkan-vs-rocm side-by-side + best-config highlight.


---

## 21. Adoption integration (Lemonade + ODS)

This section synthesizes the six cluster analyses of the Lemonade + ODS adoption candidates into one addendum. Sources are marked `L` (Lemonade), `O` (ODS), `L+O` (both). Every candidate is dispositioned as **dup** (already built/speced — plan should absorb any extra detail noted), **new** (genuine gap — see §21.x subsections), **decision** (scope fork — see §21 Decisions), or **out** (not applicable to hal0's architecture).

Verification altitude: all findings below were spot-checked against `rework/descar` code. Where a candidate is stronger or weaker than hal0's existing implementation, that is called out so the plan text can be corrected rather than silently re-litigated.

---

### 21.A Mapping table — every candidate by disposition

#### 21.A.1 DUP — already built or fully speced (absorb the noted extra detail)

| Candidate | Src | Maps to (plan §/spec/code) | Absorb into plan |
|---|---|---|---|
| POST /v1/pull SSE {file,bytes,percent} + complete/error; raw-HF pull | L | `api/routes/models.py` POST `/{id}/pull` + `/pull/stream` + `/pull/status`; ML-2 (fileset) | Note hal0 has NO `user.X` namespace — flat id space already accepts arbitrary `hf_repo`/`hf_url` via `/inspect`+pull, so any repo is raw-pullable. |
| POST /v1/pull background job survives UI reload | L | `_schedule_pull_task`, GET `/pulls`, `_reconcile_persisted_pull_job` | hal0 is stronger: jobs persist + reconcile across full `hal0-api` restart, not just UI reload. |
| GET /v1/pull/variants (enumerate GGUF quants/mmproj/top-5) | L | POST `/inspect` (5-min TTL cache, returns variants[]+tags) | Same capability, different verb; no gap. |
| GET /v1/downloads + control {pause,cancel,remove} | L | GET `/pulls`, POST `/{id}/pull/cancel`, DELETE `/pulls/{id}` | Only `pause` missing (cancel + range-resume exists). One-line follow-up on the pull-cancel route if/when ML-2 lands; not a new lane. |
| Host-agent /v1/model/{list,status,download,activate,delete} | O | §7.2 (one hal0 user + narrow privileged helper) | Confirm not orphaned; the "don't run as root" goal is met without a separate daemon. See Decision D1. |
| POST /v1/unload (specific/all) + POST /v1/delete | L | POST `/{name}/unload`, DELETE `/{id}` | Exists. |
| Per-model recipe_options.json keyed by canonical id | L | §7.1a/§8.2 model row (profile, extra_args, n_gpu_layers, chat_template, mtp, jinja) | Note in §7.1a: relational SQLite model-row supersedes a flat per-model JSON file — candidate fully absorbed, no new format. |
| extra_models_dir drop-in dir + `extra.` namespace | L | `config/schema.py` `ModelsConfig.roots: list[str]` + `registry/discover.find_candidates` | hal0 already scans multiple roots; no `extra.` id-namespace needed (one flat registry). |
| Multi-shard `gguf_parts[]` | O | ML-2 `plan_fileset`, `SHARD_RE`, discover stops deleting shards | Speced + goes further: revision-pinned enumeration, deterministic mmproj tiebreak, whole-fileset update-detect. |
| hal0 model apply one-shot (.env+config+reseed+restart) | O | §7.5/§8 `SlotConfigStore` ChangeSet + `stacks/apply.StackApplyEngine` | Atomic multi-file ChangeSet is a more robust version of ODS's env-swap-and-restart. |
| Model bundles/collections ("coding rig"/"vision rig") | L | `stacks/apply.StackConfig`/`StackApplyEngine` | This IS hal0's existing Stacks concept. |
| Two-stage idle degradation (downsize KV → evict) | L | `slots/manager._sweep_idle_once` (Stage-1 soft relabel, Stage-2 unload) | Keep the architectural note: llama-server allocates KV statically at ctx_size, so Stage-1 is bookkeeping-only (dashboard label); Stage-2 full unload is the only real reclaim. Docstring already states this. |
| Provider EngineAdapter boundary (URL norm, bearer, capability probe) | L+O | `providers/base.Provider` ABC + `api/routes/providers.test_upstream` | Split already clean. See §21.6 for the one gap (formalize the 4-state error enum). |
| Unified `<provider>.<model>` namespace in /v1/models | L | `dispatcher/router` registry-first + passthrough-cache | hal0 resolves by exact id/cached advertisement, not name-prefix; only relevant if Decision D1(routing) adopts fallback. |
| drop_params / master_key-from-env / per-mode required-key | O | n/a — LiteLLM-specific | hal0 has its own router; `master_key` covered by `HAL0_API_KEY`/`HAL0_ADMIN_API_KEY` (§1 hardening). |
| Per-provider path norm (/v1 vs /api/v1), enable_thinking:false, 900s timeout | L+O | `UpstreamEntry.url` (free-form base URL) + `normalize/thinking.py` + `UpstreamEntry.timeout_seconds` | `enable_thinking` normalization already exists per-slot; bump `timeout_seconds` (default 300s) per-upstream if a slow cloud model needs 900s. |
| enable_dgpu_gtt combined pool in capability checks | L | `hardware/probe.py:407-416` `max(vram_total, gtt_total)` | hal0's `max()` is MORE correct than ODS's "combined pool"/naive-sum framing (VRAM+GTT overlap in unified memory — a sum double-counts). |
| -ngl 99 default + tight per-model ctx | L+O | §7.1a; `-ngl 999` already default in every seed profile | Plan not weaker, just not-yet-executed; §7.1a already covers consolidating `-ngl` (set in 4 places). |
| HSA_OVERRIDE_GFX_VERSION + ROCM_PATH; verify /dev/kfd, renderD*, GIDs | O | `install.sh`, `preflight.sh:~449-676`, `providers/_gpu.py` | Already exceeds candidate — `qwen3tts.py` deliberately withholds HSA_OVERRIDE to avoid slow MIOpen fallback (nuance ODS misses). No absorption. |
| /metrics (Prometheus) + /live | O | §13.1 + `/api/metrics/prometheus`, `/api/health` | `/api/health` = requested `/live` (no new endpoint). But existing `/metrics` is slot-lifecycle-only + explicitly UNAUTHENTICATED — conflicts with candidate's "root-only/bearer" and §1 auth lane. See Decision D2. |
| WS /logs/stream (snapshot+live, resume via seq) | O | `api/routes/logs.py`, `slots.py:1468` (SSE) | Implemented as SSE, functionally equivalent for a LAN dashboard. Verify resume-via-seq exists; if not, small fold-in, not a new subsystem. |
| Capability profile artifact (gfx_target/rocm_version/rocmfp4_supported) | O | `hardware/` probe → `hardware.json` (`HardwareInfo`) + `capabilities/` | Do NOT add a 2nd capability artifact (Phase-2 is deleting `capabilities.toml` double-bookkeeping). Extend `HardwareInfo` with the 3 fields + strict validation. |
| Telemetry: emit openinference.* AND gen_ai.* in one OTLP payload | O | §13.1 (Langfuse/OTLP off-by-default) | Zero telemetry code exists today (Langfuse is a separate CT105 podman stack, unwired). Fold "both attribute families in one span" as the format spec into §13.1; needs the §7.6 request seam first. |
| Redaction toggles + runtime on/off + /telemetry/flush | O | §13.1/§13.6 | No pipeline to toggle yet; fold as impl detail when the export is built (thinking blocks large+sensitive). |
| Reasoning normalization (canonicalize </think> variants) | O | §7.6 + `toolloop/engine._THINK_RE` | Largely done (one canonical regex); only extra closing-tag variants missing — one-line add when §7.6 lands. |
| Backend contract JSON per device | O | §7.1b `RUNNER_IMAGES` | WIDEN §7.1b with `public_api_port`/`public_health_url`/`provider_url` — one artifact, not a 2nd `config/backends/*.json`. See Decision D2. |
| Model-family args config (checkpoint_regex, 3-layer precedence) | O | §7.1a `FAMILY_DEFAULTS` + §7.1d `Model.architecture` | Core need covered (keyed off `architecture`, not filename regex); note candidate's `checkpoint_regex`/`enable_regex_match` as an alternative matching strategy to weigh in §7.1a. |
| backend_url in /v1/health (child /metrics /props) | O | §6 /v1/health item (§21.3) | Just a payload field of that endpoint; fold in. |
| /v1/reranking (→ llama.cpp /v1/rerank) | L | `v1.py:1098` `/rerankings` + `:1103` `/rerank` | Shipped + documented in `first-chat.mdx`. |
| /v1/embeddings (encoding_format) | L | `v1.py:1083` `/embeddings` | Shipped. Residual: doc the LangChain `check_embedding_ctx_length=False` gotcha in §21.12 client docs. |
| /v1/audio/transcriptions | L+O | `v1.py:1112` (multipart) | Shipped (the streaming gap is §21.9 /v1/realtime). |
| hal0 CLI thin dispatcher + verbs + completion | O | `cli/main.py` typer app (`add_completion=True`) | Cleaner than hand-rolled `lib/hal0-*.sh`; per-verb `hal0-x` aliases not worth it. |
| hal0 agent start/stop/status/logs (systemd) | O | `cli/agent_commands.py`, `agent_shim.py`, `hal0-agent@.service` | Deeper than candidate; no macOS/launchd/nohup fallback needed (LXC/systemd only). |
| hal0 bench (TTFT+TPS, JSON, --compare) | O | §20 Bench rework + §20.1 auto-tuner | Plan stronger. Add only the "needle" long-context-position scenario to §20's target list. |
| mDNS announcer (hal0.local, re-announce) | O | `services/mdns.py` (avahi) | Avahi inherits LAN/loopback gating + event-driven inotify — superior to a hand-rolled zeroconf poll. |
| Dashboard per-concern routers + Vite /api proxy | O | `api/routes/*.py` (~40 files) + `ui/` | Already the architecture. |
| Hardcoded "do-not-destroy" service set | O | `services/registry.SERVICES` (static Python + per-service action allow-list) | Protection holds by construction (no config→fail-open path). Note llama-server itself is owned by `slots/manager.py`, not this allow-list — flag to §7.5/§11 only if slot-delete gains a config-driven allow-list. |
| service_id/model_id validation regex; loopback host/port probe | O | `services/systemd._UNIT_RE`, `registry/pull._SANITISE_RE`/`_SHA256_HEX_RE` | Regexes enforced at exec boundary. A generic loopback host/port probe endpoint doesn't exist — minor/low-pri; if built, apply §1's SSRF/RFC1918 gating. |
| Stable model-name strings (suffix quant/device) | L | de-facto HF-publishing convention (`Qwen3-4B-ROCmFP4-Strix`) | Codify as one sentence in §7.1/`choose-models.mdx`; no new mechanism. |
| Process-group kill on child timeout | L | `providers/container.py` `podman stop -t 20` (cgroup-scoped) | **out** — hal0 runs backends in Podman cgroups under systemd; `podman stop` is a stronger guarantee than a PGID kill. Different, already-solved architecture. |
| Layered validation (unit→container→VM→real-hw) | O | CONTRIBUTING.md α/β/γ tiers + `hal0-test` LXC gate | Already a superset (γ runs on real Strix-Halo over SSH). Phase 4 should read "extend," not "build." |
| Capability-deferral state machine | O | release-test row statuses + `release-gate-report.json` | Coarser 4-state exists; refine existing rows + hook into pull-job states (priority #3), don't add a parallel machine. |
| Validation receipt template | O | `release-gate-report.json` + `release-check.sh` (7 gates) | Add hw/install-cmd identity + rollback/limits fields to the existing report — schema add, not new machinery. |
| Support tiers A/B/C + GPU tier map | O | `model_fit.evaluate_model_fit`, `hardware/recommend.py` | Logic is already a pure function; only the doc label missing. NAMING: call it "support class" — "tier" is double-booked (bench A/B/C, test α/β/γ). |
| Digest-pinned image refs + KNOWN-GOOD-VERSIONS.md | L+O | `manifest.json` + `update-toolbox-digests.sh` + gate #4 | Shipped + enforced. Each digest atomically encapsulates its llama.cpp/ROCm versions, so KNOWN-GOOD-VERSIONS.md is largely redundant — a human-readable companion table is COULD, not MUST. |
| Agent profile YAML (id/model/system_prompt/tools/…) | O | `agents/personas.Persona` + `/api/agents/{id}/personas` (§7.3/§7.4) | ~80% present. Absorb missing fields via §21.13 (fallback_model, routing_rules, tool_config, schema_version). Keep the name **persona** — "profile" already = model-runner profiles. |
| Local-first model + named fallback per profile | O | `persona.preferred_model` + dispatcher Rule 9 (ADR-0023) | Global fallback exists; per-persona chain missing → §21.13. Compose with Decision D1(routing), don't build a 2nd mechanism. |
| Capability/feature catalog (services_any, vram/disk gates) | O | `model_fit.evaluate_model_fit`, `capabilities/profile_fit.py` | HW-gating half exists. `services_any` OR-disjunction N/A (memory=Hindsight, single locked backend) unless Decision D5 reopens. |
| POST /run-agent → SSE tool events | L+O | `api/routes/board_chat.py` POST `/api/board/chat` | **DONE, near-verbatim** ({token,thinking,tool_call,tool_result,done,error}, shared toolloop). Mark DONE so nobody rebuilds. Follow-on in §21.13: generalize board-framed loop to slot-agnostic once §7.6 lands. |
| Voice pipeline (STT→LLM→TTS) as canonical workflow | O | §19 voice stack + §7.1d ASR/TTS modalities | Components adopted at model level; only "package as one named recipe" missing — small doc task once §19 lands. |
| toolDefinitions.json single source (UI↔server) | L | `omni_router/tool_definitions.json` + `check-tool-definitions.sh` | **DONE** incl. deferred drift-check script. Close out the drift-check in Phase 4. |
| Async job API (submit/poll/fetch) | O | `comfyui/fetch.py`, `provision.py` | **out** — ComfyUI already has job_id async submit/poll/fetch; don't generalize until a 2nd async backend ships (§2 "narrow every abstraction to its single concrete"). |

#### 21.A.2 NEW — genuine gaps (see §21.x subsections)

| Candidate | Src | Priority | Lands in |
|---|---|---|---|
| amdgpu `gttsize=120000` modprobe | O | MUST | §21.1 (host tuning) + preflight WARN |
| GRUB `amd_iommu=off` | O | MUST | §21.1 + preflight |
| `tuned-adm profile accelerator-performance` | O | MUST | §21.1 + preflight |
| `ppfeaturemask=0xffffffff`, `gpu_recovery=1` | O | MUST | §21.1 (same modprobe.d file) |
| ttm `pages_limit`/`page_pool_size` (derived from gttsize) | O | MUST/SHOULD | §21.1 |
| `vm.swappiness=10`, `vm.vfs_cache_pressure=50` | O | MUST/COULD | §21.1 + preflight |
| `update-initramfs -u` + reboot gate | O | MUST | §21.1 (loud, non-skippable) |
| Build/verify gfx1151 HIP arch + refuse-to-start guard | L+O | MUST (highest-value in cluster §2) | §21.2 + §7.1b |
| Persist ROCm kernel cache + generous cold-JIT `wait_for_ready` (~15-20 min) | L | SHOULD | §21.2 (agent_shim `_READY_TIMEOUT_S=90` far too short vs ~12-min ROCmFP4 JIT) |
| `--parallel` tier-scaled (Strix Halo → 8-12) | O | MUST | §21.2 (mechanism-complete, policy-missing; measure via `server_ab.py --mode batch` first) |
| ROCM_PATH resolution order + rocm_channel/rocm_bin pin | L | SHOULD | §21.2 (toolbox/build lane) |
| POST /v1/load blocked-arg denylist | L | SHOULD | §21.7 / §7.1a (`MANAGED_ARGS_DENYLIST`) |
| Managed-args reject in extra_args (--model/--ctx/--host/--port/-ngl) | O | MUST | §21.7 / §7.1a (`slots/argv.py`) — same code path |
| Bootstrap fast-start model → background swap to full | L+O | COULD | §21.8 (mechanism = existing `swap_slot`; only first-boot policy new) |
| `--source huggingface\|modelscope` + HF_ENDPOINT mirror | L | COULD | §21.8 (fold into ML-2 fileset as optional param) |
| `max_loaded_models` per model-type LRU | L | SHOULD/low | §21.10 (per-modality budget on P3-slots reaper) |
| `auto_evict` + threshold_pct (GTT-aware) | L | SHOULD (elevate) | §21.10 — **single most concrete gap**: pressure probe reads raw `/proc/meminfo` not GTT-aware `CapacitySnapshot` (the user's own "pve GTT hidden memory" blind spot) |
| Operator-settable pin + protect manual /unload of pinned | L | SHOULD | §21.10 (`SlotConfig.pinned` + 409/force on manual unload) |
| `eviction_score = idle/(load×weight)` | L | COULD | §21.10 (optional reaper refinement) |
| /v1/health per-model detail | O | MUST | §21.3 (as `GET /api/models/health`) |
| /v1/stats + /v1/system-stats | O | MUST | §21.3 (read API over §13.3 tables) |
| /v1/system-info (hw enum + backend install state) | O | MUST | §21.3 (fold `/api/hardware`+`/api/features`+ §7.1b lifecycle) |
| hal0 doctor `--json`/`--report` + stable diagnosis IDs | O | MUST | §21.4 (retrofit existing 1420-line doctor onto `_diagnosis` dataclass) |
| Support bundle (redaction, TSV, ROCm captures) | O | MUST | §21.4 (`hal0 doctor bundle`) |
| Backend lifecycle state (installed/update_available/…) | O | SHOULD | §21.3/§21.6 (§7.1b registry field) |
| backend_versions.json + rocm_arch_overrides + startup gfx-guard | O | MUST | §21.2/§21.6 (feeds `HAL0-GFX-TARGET-UNSUPPORTED`) |
| recipe:backend colon selector | O | COULD | §21.6 (§7.1b CLI sugar) |
| Auto-select installed-on-disk beats preference + `prefer_system` | O | MUST | §21.6 (§7.1b selection logic) |
| /v1/models extensions (recipe/checkpoint/labels/downloaded) + show_all filter | L | MUST | §21.5 (`v1.py`) |
| POST /v1/messages (Anthropic) + `hal0 launch claude` | O | MUST | §21.9 (highest strategic value — unlocks Claude Code) |
| /v1/tokenize (+/detokenize) | L | SHOULD | §21.5 (thin proxy to llama-server native) |
| WS /v1/realtime (OpenAI Realtime, PCM16, VAD) for OpenWhispr | O | MUST | §21.9 (own subsection; depends on §19 whisper.cpp slot) |
| Multiple path prefixes (/v0, /api/v1) | O | SHOULD | §21.5 (extra `include_router` mounts, near-zero cost) |
| Terminal chat REPL (`/think`, `--no-stream`, strip reasoning) | O | SHOULD | §21.14 (`hal0 chat`) |
| VRAM-scaled sub-agent concurrency + per-persona timeout | O | SHOULD | §21.13 (gate on Phase-3 capacity signal; replace fixed `_MAX_LOOP_ROUNDS=8`) |
| Regex `routing_rules` pre-classifier | O | SHOULD | §21.13 |
| safe/dangerous shell-exec command list | O | SHOULD | §21.13 (with §14.1 security fast-track) |
| Client-connection docs (LangChain/Continue/Cursor/n8n) + troubleshooting table | O | MUST | §21.12 (`docs/guides/connect-clients.mdx`) |
| `hal0 setup-cursor`/`setup-continue` config writers | O | SHOULD | §21.12 (with §17 installer/CLI overhaul) |
| Offline/air-gapped mode (`--offline`, bundle default GGUFs) | O | SHOULD/COULD | §21.12 (§17-adjacent note; no telemetry exists to drop today) |
| PR template + high-risk-change map | O | MUST | §21.15 (`.github/PULL_REQUEST_TEMPLATE.md`) |
| Stable-patch triage 4-question tree | O | SHOULD | §21.15 (CONTRIBUTING.md; channel-count-independent) |
| hw-support-class doc table (from `model_fit`) | O | SHOULD | §21.15 (name "support class") |
| Network-exposure-policy CI test / ports contract / golden-paths | L+O | MUST/SHOULD | §21.11 (config contracts) |
| EngineAdapter 4-state error enum (unreachable/auth/model-missing/unsupported) | L+O | low | §21.6 (formalize on remote path; useful if Decision D1 routing lands) |

#### 21.A.3 DECISION — scope forks (see §21 Decisions D1–D8)

| Candidate | Src | Decision |
|---|---|---|
| Local+cloud fallback ladder ({local:[cloud]}, num_retries, shuffle) | O | D1 |
| Privileged host-agent daemon (own bearer, per-service Lock, 16KB cap) | O | D1 |
| Two-secret split (dashboard key ≠ agent key) | O | D1 |
| Backend-contract JSON vs widen §7.1b registry | O | D2 |
| Auth on /metrics (root-only/bearer vs current unauthenticated) | O | D2 |
| Ollama-compat surface (:11434, /api/tags,/chat,/generate,/show,/ps,/embed) | O | D3 |
| Host-level Strix-Halo tuning blast radius (shared PVE host) | O | D4 |
| Document-RAG as first-class + services_any disjunction | O | D5 |
| Workflow catalog / generic DAG schema | O | D6 |
| AI-CI automation (nightly-review, claude-review, ai-triage, autonomous-scanner) + guardrails doc + prompt discipline | O | D7 |
| Five-channel release model vs locked §7.7 "remove nightly" | O | D8 |
| Upstream OSS PRs into Continue/Open WebUI | O | D3 |
| hal0-recipes standalone repo vs in-repo `bundles/` | L | D3 |

#### 21.A.4 OUT — not applicable to hal0's architecture

| Candidate | Src | Why out |
|---|---|---|
| `--enforce-eager` device-class default | L | vLLM/CUDA-graph-capture concept; hal0 is llama.cpp/HIP only — no equivalent flag, failure mode doesn't exist. |
| Process-group kill on child timeout | L | Podman cgroup `stop` is stronger; PGID trick only needed for bare subprocesses. |
| Async job API generalization | O | ComfyUI already covers the one async backend; don't generalize prematurely. |
| LiteLLM `drop_params`/`master_key`/required-key | O | hal0 has its own router, not a LiteLLM front. |

---

### 21.1 Host/hypervisor Strix-Halo kernel tuning (Proxmox layer — outside hal0's own install) — MUST, highest concrete ROI

Genuinely new lane, absent from plan and repo (grep: no `gttsize`/`modprobe.d`/`tuned-adm`/`sysctl` artifacts anywhere in `installer/` or `packaging/proxmox/`). These are kernel/GRUB/tuned-adm/sysctl settings that apply to the **PVE hypervisor hosting the new "halo" LXC**, NOT to hal0's own install path — an LXC shares the host kernel and cannot `modprobe`/GRUB/`tuned-adm`/`sysctl` the box. Scope is explicitly separate from §17's `hal0 provision --stage=system|services` (which only ever runs inside the halo guest).

**Ships as** a one-time, idempotent host-prep script, sibling to `packaging/proxmox/hal0-test-template/provision.sh` (e.g. `packaging/proxmox/host-tune-strix-halo.sh`) that:
- Writes `/etc/modprobe.d/amdgpu-hal0.conf`: `gttsize=120000`, `ppfeaturemask=0xffffffff`, `gpu_recovery=1`, plus ttm `pages_limit`/`page_pool_size` **derived from the chosen gttsize** (document the formula; don't hardcode two independent constants that can drift).
- Appends `amd_iommu=off` to `GRUB_CMDLINE_LINUX` (document that `iommu=pt` is NOT equivalent).
- Sets `tuned-adm profile accelerator-performance` (idempotent set + verify).
- Writes `/etc/sysctl.d/99-hal0-strix.conf`: `vm.swappiness=10`, `vm.vfs_cache_pressure=50`.
- Runs `update-initramfs -u`, then prints a **loud, non-skippable required-reboot banner and exits nonzero until an env var confirms the reboot happened** (matches hal0's "never auto-hide, always surface" posture).
- Documents the BIOS UMA Frame Buffer minimum as a precondition (not scriptable).
- Is **NOT auto-invoked by any hal0 installer path** (see Decision D4 — host-wide blast radius needs explicit opt-in).

**Verification (into `hal0 doctor`/preflight, MUST):** add WARN-only read-checks to `installer/lib/preflight.sh` alongside the existing `/dev/kfd`/`/dev/dri/renderD*` checks — read `/sys/module/amdgpu/parameters/*` (gttsize/ppfeaturemask), `/proc/cmdline` (amd_iommu), `sysctl vm.swappiness`, `tuned-adm active`. All are readable from inside the halo LXC because they reflect real host-kernel/global state, not namespaced values. Surface as a single "host not tuned for Strix Halo, expected +X% inference" WARN, never a hard failure (hal0 runs correctly untuned, just slower). Flag every numeric perf claim (e.g. amd_iommu=off +2–6%) as measure-first via `hal0-tune`, not blind-adopt.

**Coordination:** device_class-scoped runtime concerns that ARE container-level (arch guard, cold-start timeouts, `--parallel` tiers) stay in §21.2/§7.1b, not here. §17 explicitly does not extend into this scope.

### 21.2 gfx1151 arch-guard + ROCm cold-start/kernel-cache + --parallel tiers — MUST

Container/process-level companions to §21.1, slotting into existing plan machinery (§7.1a flags, §7.1b runner registry, §20 bench).

**(a) gfx1151 refuse-to-start guard (highest-value item in cluster §2).** Confirmed absent — no `system_info`/HIP-arch probe anywhere in `providers/container.py` or `mcp/probes.py`, despite `mcp/probes.py` already decoding `gfx_target_version` → `gfxNNNN`. The failure mode is **silent garbage (all-`?`) output, not a crash.** Add `required_hip_archs` to the §7.1b `RUNNER_IMAGES` entry and a startup probe (reuse the existing gfx-decode helper) that checks the launched llama-server's reported `system_info` HIP archs against the registry before marking the slot READY. On mismatch, transition WARMING→failed (never a lying READY). Surface pass/fail as doctor diagnosis ID `HAL0-GFX-TARGET-UNSUPPORTED` (§21.4). Pair with a `backend_versions.json` artifact (or a version/digest field folded into the widened §7.1b registry) recording the pinned llama.cpp build + `rocm_arch_overrides` suffix per runner.

**(b) ROCm cold-JIT persistence + timeouts (SHOULD).** The MIOpen `/cache`-mount pattern (`MIOPEN_USER_DB_PATH`/`MIOPEN_CUSTOM_CACHE_DIR`) exists ONLY for `providers/qwen3tts.py:181-192`, not the main ROCm llama-server containers in `providers/container.py`. Separately, `cli/agent_shim.py:383` hardcodes `_READY_TIMEOUT_S = 90.0` — far short of the ~12-min ROCmFP4 cold-JIT in the user's own `rocmfp4-quant-procedure` memory. Fix: (1) verify what the ROCmFPX fork actually JIT-caches to disk (rocBLAS/hipBLASLt tuning DB vs fork kernels), then extend qwen3tts's proven `/cache`-mount pattern to `container.py` if there's a real cacheable artifact; (2) audit `agent_shim._READY_TIMEOUT_S` and the `slots/manager.py` WARMING ceiling against the ~12–20 min reality and raise. Tie to §7.1b: make `cold_start_timeout_s` a device_class-scoped registry field, not a global constant.

**(c) `--parallel` tier-scaling (MUST — mechanism-complete, policy-missing).** Fully wired end-to-end (`providers/container.py`, `slots/argv.py`, `config/schema.py`, slot_view) but every seed profile hardcodes `--parallel 1`; `CHANGELOG.md:489` already flags defaults "stay --parallel 1 pending the on-box -np sweep (server_ab.py --mode batch)". Do NOT blind-adopt ODS's flat 8-12 — `hal0-tune` rules out applying a community claim without local measurement, and `server_ab.py --mode batch` is the exact sweep tool. Run the sweep now that the seam exists, then encode the winning tier-scaled defaults as a device_class-scoped field consumed by §7.1a flag resolution (and/or §7.1b registry), superseding `--parallel 1` in `installer/etc-hal0/profiles.toml` + `config/schema.py` seeds. Ties to §20 (np/parallel already a sweep axis).

**(d) ROCM_PATH build reuse (SHOULD, low-urgency, toolbox lane).** Document the resolution order (`ROCM_PATH` env → `rocm-sdk` path → `/opt/rocm`) + a `rocm_channel` (stable/nightly) + `rocm_bin` pin in `installer/agent-skills/hal0-quantize/` (already has `rocmfpx-env.sh`/`presets.md`) and `docs/design/container-image-overhaul.md`. Not urgent enough to block the model-layer epic or §17.

### 21.3 Introspection endpoints — MUST

hal0 has `/api/status`, `/api/health(/system)`, `/api/metrics` (stub), `/api/metrics/prometheus` (slot-lifecycle only), `/api/logs/stream` — but not the specific read surfaces ODS names. Keep hal0's `/api` naming, not literal `/v1`.

- **`GET /api/models/health`** (MUST) — per-model `{checkpoint,last_use,type,device,pinned,recipe,pid,recipe_options,backend_url}` shape; extend `health.py`'s `/api/status` merge logic, read `SlotManager` + the §13.3 `slot_sample`/`request_metric` tables. Sequence after ML-1 (needs those tables). `backend_url` exposes the child llama-server `/metrics`//`/props` (not proxied).
- **`GET /api/stats` + `GET /api/system-stats`** (MUST) — thin read API over §13.3's `request_metric`/`slot_sample` (TTFT/tok-s/vram/gpu%), which §13 defines but never exposes a read API for. This becomes the dashboard's data source instead of ad-hoc dashboard-only SQL. Add as an explicit bullet under §13.7 sequencing.
- **`GET /api/system-info`** (MUST) — one consolidated endpoint folding `/api/hardware` + `/api/features` + §7.1b's new backend lifecycle-state field (`installed/update_available/update_required/installable`), rather than three overlapping surfaces. Feeds a future setup-wizard "install this backend" action.
- **`/api/metrics/prometheus`** — expand body once §13 T1/T2 aggregation lands (currently only `hal0_slot_up/state/ready_total`). `/api/health` already = the requested `/live` (zero-work liveness) — no new endpoint. Auth on this route is Decision D2.

### 21.4 hal0 doctor rework + support bundle (new §13.8) — MUST

`hal0 doctor` already exists (1420 lines: perms/models/profiles/migrations/toolbox-pull/verify/logs + `preflight.sh` shell-out) — this is a retrofit, not greenfield (~1–2 weeks). Missing: a stable diagnosis-ID taxonomy (`HAL0-GFX-TARGET-UNSUPPORTED`, `HAL0-ROCM-LIB-MISSING`, `HAL0-MODEL-FILE-MISSING`…), a structured `_diagnosis(id,severity,confidence,evidence[],next_steps[])` return type, a `--json` flag, and structured autofix hints (beyond ad-hoc `repair_flm_store`/`repair_tree_group_share`).

Retrofit the existing checks onto one shared `_diagnosis` dataclass + `--json` renderer, adding IDs as each check is touched. Add **`hal0 doctor bundle`** (support bundle): redact KEY/TOKEN/Bearer from config dumps, emit a command-status TSV, layout system/config/diagnostics/logs/manifest, include `rocm-smi --showall` + `rocminfo` captures. Same PR/section. Sequence after §21.2's gfx-arch guard (needs a diagnosis ID) and §13's metrics tables (evidence source).

### 21.5 OpenAI-compat surface extensions — MUST/SHOULD

- **`/v1/models` extensions** (MUST) — current `GET /v1/models` (`v1.py:673`) emits only `{id,object,created,owned_by,name,context_length}`; the registry stores richer fields (`labels`, checkpoint/recipe) never surfaced on the read path. (1) Extend `hal0_slot_alias_models()` + the upstream-catalog loop to emit `labels`, `recipe`/`checkpoint`, `downloaded`; (2) alias `context_length` → `max_context_window` (or emit both); (3) add `show_all` query param (mirror the `owned_by` filter at `v1.py:807`) defaulting to hiding non-text-modality raw upstream catalog entries (e.g. image-gen models leaking in). Low-risk, additive; Claude Code probes this first, so it precedes §21.9 /v1/messages.
- **`/v1/tokenize` + `/detokenize`** (SHOULD) — absent; thin proxy to llama-server's native endpoints, routed by slot/model. Ties to §13's `ctx_used` metric + client-side prompt-fitting.
- **Extra path prefixes `/v0`, `/api/v1`** (SHOULD) — routers mount only at `/v1` (`api/__init__.py:1281-1282`); add duplicate `include_router` calls for clients that hardcode `/api/*`. Near-zero cost; rides along with the /v1/models work.

### 21.6 Backend/engine abstraction hardening (extend §7.1b) — MUST/SHOULD

- **Widen `RUNNER_IMAGES`** with `public_api_port`/`public_health_url`/`provider_url` so the registry is the single backend-contract source (currently scattered in `container.py` URL/port assembly). One artifact, not a 2nd `config/backends/*.json` — see Decision D2.
- **Managed-args denylist** (MUST) — see §21.7.
- **Backend lifecycle state** (SHOULD) — add `installed/update_available/update_required/installable`, computed by comparing pinned digest vs `podman image inspect`, surfaced via `/api/system-info` (§21.3). `updater.py`'s `update_available` today is whole-hal0-release only.
- **Auto-select** (MUST) — §7.1b covers registry shape but not selection when multiple backends exist. Prefer already-installed-on-disk over preference-order (avoid an unnecessary multi-GB pull), with a `prefer_system` escape hatch for operators managing their own ROCm.
- **`recipe:backend` colon selector** (COULD) — minor CLI sugar resolving into registry keys; follow-up once the registry exists.
- **EngineAdapter 4-state error enum** (low) — formalize `unreachable/auth/model-missing/unsupported` on the remote-upstream path (`test_upstream` is ad-hoc strings today). Only worth it if Decision D1(routing) adds retry logic.

### 21.7 Managed-args denylist (part of §7.1a flag resolution) — MUST

`[server].extra_args` is a free-form `shlex`-split string appended last-wins in `container.py`'s `resolve_argv`, with NO denylist anywhere in `slots/argv.py` (`merge_flags`/`normalize_argv`). A slot's `extra_args` (or a future request-level `llamacpp_args` on `/load`) can pass `--model`/`--ctx-size`/`--host`/`--port`/`-ngl` and silently clobber the OpenAI-shim contract or redirect the model file — a real correctness/security gap (a compromised board_chat/brain tool call could exploit it). Add a hardcoded `MANAGED_ARGS_DENYLIST` checked in `merge_flags`/`normalize_argv` before the `extra_args` segment is appended, erroring loudly (400/config-validation) instead of producing a broken running slot. Same merge algorithm as the §7.1a 5-tier precedence rewrite (`request→recipe_options→arch_defaults→env→default`) — land in the same PR.

### 21.8 Model-management niceties — COULD

- **Bootstrap fast-start → background swap** (COULD) — mechanism exists (`slots.swap_slot`: unload+load on a live slot with registry pre-validation); only first-boot policy is new (seed a tiny default model at install, auto-swap once a larger background pull completes). Thin installer/first-run enhancement, not a new primitive.
- **`--source huggingface\|modelscope` + HF_ENDPOINT mirror** (COULD) — no modelscope/mirror support exists; low value for a single-operator LAN box. Fold as an optional `hf_download_url` param into ML-2 fileset work.

### 21.9 Anthropic + Realtime + streaming STT — MUST (highest strategic value)

- **`POST /v1/messages` (Anthropic) + `hal0 launch claude`** (MUST) — fully absent. Single highest-strategic-value item: unlocks Claude Code and any Anthropic-SDK client with one command. (1) New route in `v1.py` implementing the Messages API shape — translate `{system, messages[content-blocks], tools, stream}` into the existing OpenAI chat-completions request the dispatcher routes, and translate the response/SSE back (Anthropic's `message_start`/`content_block_delta`/`message_delta`/`message_stop` differ from OpenAI chunks — a small but real translation shim, not passthrough); reuse `hal0_chat_slot_alias_map` so `model:"agent"` keeps working. (2) `hal0 launch claude` CLI verb sets `ANTHROPIC_BASE_URL`/`ANTHROPIC_AUTH_TOKEN` (dummy) + default model, then execs `claude`. Sequence after §21.5 /v1/models (Claude Code probes it first).
- **`WS /v1/realtime` for OpenWhispr** (MUST, own subsection) — the only WS route today (`board_ws.py`) is the unrelated kanban proxy; `/v1/audio/transcriptions` is multipart-only (the "per-chunk" pattern to replace). Concrete personal motivation: the user's OpenWhispr already points at hal0's `/v1` (`whisper-v3:turbo`, per memory). New `WS /v1/realtime` accepting 16 kHz PCM16 frames + VAD config + manual/auto commit (mirror OpenAI's Realtime protocol closely so OpenWhispr's client needs minimal changes), buffering server-side. First cut: windowed re-transcription of a rolling buffer with VAD-triggered commits against the existing batch endpoint, upgraded to true streaming once whisper.cpp's server gains it. **Depends on §19's whisper.cpp large-v3-turbo GGUF slot** — sequence after that lands.

### 21.10 Multi-model memory manager (fold into P3-slots reaper) — SHOULD (D-elevate one item)

hal0's LRU idle/pressure eviction, two-stage degrade, and pinning-exempt eviction already exist and are often stronger than the candidates. Three verified gaps fold into the P3-slots `reaper.py` extraction:

- **GTT-aware pressure probe (elevate — single most concrete gap).** `_pressure_evict_once` → `_probe_host_free_mb` → `capacity._read_meminfo` reads RAW `/proc/meminfo MemAvailable` with a fixed MiB floor, NOT the already-built GTT-aware `slots/capacity.CapacitySnapshot.free_vram_mb` — exactly the user's "pve GTT hidden memory" blind spot (amdgpu GTT isn't charged to normal RAM accounting). Switch the probe to `CapacitySnapshot.free_vram_mb/total_vram_mb` (or a direct rocm-smi/sysfs GTT read); optionally express the floor as `threshold_pct` of total, not only an absolute MiB.
- **Operator pin + manual-unload protection.** Automatic-eviction exemption exists (`_PINNED_BY_DEFAULT` frozenset: chat/agent/npu) but there's no `SlotConfig.pinned` field and `POST /{name}/unload` works unconditionally on a pinned anchor. Add `SlotConfig.pinned: bool` (overlay onto `_PINNED_BY_DEFAULT`) + require `force=true` (else 409 `slot.pinned`) on manual unload/delete.
- **Per-modality budget (SHOULD, low).** Global `[slots].max_slots` cap + LRU exist but no per-type quota ("never >1 vision model resident"). hal0's slots are fixed/named/role-typed with swap (not a dynamic same-type pool like Lemonade), so this is a smaller optional refinement, not a MUST — don't block the reaper extraction on it.
- **Weighted eviction score (COULD)** — replace plain idle-LRU with `idle/(load×weight)` to protect slow-to-reload models; optional, low-value.

### 21.11 Config contracts (network-exposure-policy CI + ports + golden paths) — MUST/SHOULD

Absorbs the "default-deny bind", id-validation, and loopback-probe hardening themes from §8 into a concrete CI/config contract lane (currently scattered):
- **Network-exposure-policy CI test (MUST)** — a test asserting no route/service binds beyond the intended LAN/loopback posture without going through §1's auth path; codifies the "unauthenticated by convention" surfaces (e.g. `/api/metrics/prometheus`, board routes) as an explicit allow-list the test guards, feeding Decision D2.
- **Ports contract (SHOULD)** — a single declared source of the ports hal0 and its companion services own (dashboard/api, slot ports, mDNS), drift-validated, so a slot's port can't silently collide (ties to §21.7 managed-args and the `_UNIT_RE`/`_SANITISE_RE` validators).
- **Golden-paths (SHOULD)** — pin the canonical on-disk layout (config/models/cache/logs) as a validated contract feeding `hal0 doctor` evidence and the support bundle (§21.4).

### 21.12 Client onboarding + docs — MUST/SHOULD

- **`docs/guides/connect-clients.mdx`** (MUST) — no per-client guide exists (grep confirms). Cover LangChain (`ChatOpenAI(base_url=…)`, note `check_embedding_ctx_length=False`), Continue (config.json provider block), Cursor (custom OpenAI-compatible endpoint), n8n (OpenAI node base_url), each with the host-vs-container base_url distinction (follow the `HAL0_OPENWEBUI_PUBLIC_URL` template in `first-chat.mdx`). Bundle a **troubleshooting table** per integration (base_url mistakes, no-built-in-auth header confusion, alias-not-found, SSE-parsing gotchas) into the same doc.
- **`hal0 setup-cursor`/`setup-continue`** (SHOULD) — two small CLI verbs writing the ~8-line client config pointing at `/v1` + a default slot alias, same shape as `cli/setup_command.py`'s first-run wizard. Lands with §17 (Lane E); pairs with `hal0 launch claude` as the third onboarding one-liner.
- **Offline/air-gapped mode** (SHOULD/COULD) — no such mode today (the only "offline" concept means install-time-before-api). There is also no telemetry to drop (`registry/update_check.py` only hits HF on-demand via CLI). So this is: (1) a `--offline` flag skipping HF update-check + first-run network probes, (2) optionally bundling a default small LLM+embedding GGUF for first-boot-without-pull, (3) local-RAG web-search fallback (blocked on Decision D5). Short §17-adjacent note, sequenced well after the higher-value §21.9 items.

### 21.13 Persona schema hardening (new §10.x) — SHOULD

Extend `agents/personas.Persona` + its TOML schema rather than introducing a new "agent profile" concept (avoids a 3rd meaning of "profile" alongside model-runner profiles and `profile_fit`):
- Add `fallback_model` (composes with Decision D1's local→cloud ladder; do NOT build a 2nd fallback mechanism vs dispatcher Rule 9).
- Add `routing_rules: list[{pattern, target}]` — a regex pre-classifier checked before the label-based dispatcher route.
- Add `tool_config` (structured per-tool config, not just the existing `tools_allowed` glob).
- Add `safe_commands`/`dangerous_commands` regex lists scoped to the `shell_exec` tool, rejected before the sandbox call — land with §14.1's security fast-track (same PR as auth fixes; §14.1 already flags `AUTONOMOUS_WRITE_TOOLS` running without approval).
- Add `timeout_s` per persona and gate `OmniRouter`/`board_chat` concurrent loops behind the Phase-3 capacity-manager's VRAM-headroom signal instead of the current fixed `_MAX_LOOP_ROUNDS=8` (`omni_router/router.py`, unbounded `asyncio.gather` today).
- Stamp `schema_version` on the Persona TOML.
- Once §7.6's shared `toolloop/engine.py` lands, generalize `board_chat`'s SSE loop (currently board/`caller_slot_name`-framed) into a slot-agnostic run-agent entry point any persona can drive — the SSE contract itself needs no change.

### 21.14 `hal0 chat` terminal REPL — SHOULD

New `cli/chat_commands.py` talking to local `/v1/chat/completions` over a slot alias, with `/think on|off|default` toggling the existing thinking-policy injection (`normalize.messages` already has this step) and stripping reasoning tokens from the REPL's in-memory history before the next turn (reuse dispatch's reasoning-separation logic, don't re-implement). `--no-stream` is a thin flag on the existing SSE client path. Useful for SSH/headless boxes; prevents reasoning-token context bloat. Self-contained, no cross-lane coordination.

### 21.15 Release-engineering hardening (new §11.x) — MUST/SHOULD

State explicitly in Phase 4 that layered validation (α/β/γ), validation receipts (`release-gate-report.json`), and digest-pinned images (`manifest.json`) are ALREADY SHIPPED, so Phase 4 reads "extend," not "build."
- **`.github/PULL_REQUEST_TEMPLATE.md`** (MUST) — risk-grade + touched-surface checkboxes + rollback note, cross-referencing §14.1's high-risk surfaces (unauthenticated board routes, `AUTONOMOUS_WRITE_TOOLS`). No template exists today (CONTRIBUTING.md has only a proto-version).
- **hw-support-class doc table** (SHOULD) — wire `model_fit.evaluate_model_fit` as the single source; name it "support class" (NOT "tier" — already double-booked by bench A/B/C and test α/β/γ).
- **Receipt schema fields** (SHOULD) — add hw/install-cmd identity + rollback/limits to `release-gate-report.json`.
- **Stable-patch triage 4-question tree** (SHOULD) — CONTRIBUTING.md addition; channel-count-independent, lands regardless of Decision D8.

---

### 21 Decisions for the user (scope forks)

Each below is a genuine fork requiring an explicit call — not silently absorbable.

**D1 — Cloud/hybrid routing + privileged host-agent (local-first ethos).** hal0 treats cloud providers as ordinary named upstreams (Anthropic/OpenAI/OpenRouter templates, reachability probe) but has ZERO automatic runtime failover from a local slot to a cloud upstream. Adding one is a privacy/data-locality change: a request the operator believed stayed on-LAN could silently leave to a cloud provider on local failure. Options: (a) do nothing — cloud stays manually-addressed (strictly local-first, current); (b) opt-in per-model/per-persona `fallback_model` that engages only on an explicit flag, never silently; (c) full hybrid.yaml ladder with retries/shuffle (closest to ODS, weakest fit with local-first). **Recommend (b) if adopted at all, gated off by default.** Bundled with this: the **privileged host-agent** fork — ODS proposes a separate always-on network daemon with its own bearer + two-secret split (dashboard key ≠ agent key). §7.2 already achieves "don't run as root" via a narrow in-process sudo/polkit helper, which fits the locked "one hal0 user" decision. **Recommend keeping §7.2's lighter direction, pulling only ODS's concrete hardening (per-service Lock, subprocess timeouts, 16KB body cap, default-deny bind, id-validation regex) into that helper.**

**D2 — Backend-contract artifact + metrics auth (§7.1b design + cross-lane).** (i) ODS proposes a new `config/backends/{rocm,cuda,cpu}.json`; **recommend instead widening §7.1b's `RUNNER_IMAGES` with the missing `public_api_port`/`public_health_url`/`provider_url` fields** — one-artifact-vs-two tradeoff for whoever implements §7.1b to confirm. (ii) `/api/metrics/prometheus` is explicitly documented as unauthenticated "by convention," which conflicts with ODS's "root-only/bearer" ask AND §1's LAN-auth-hardening lane. **Not resolvable within §6/§7 scope — needs coordination with the §1 owner** so the metrics-auth fix isn't split across two PRs.

**D3 — Second protocol surfaces + external repos.** (i) **Ollama-compat** (:11434, `/api/tags,/chat,/generate,/show,/ps,/embed`): hal0 only uses Ollama as an upstream it proxies TO, never a surface it exposes. Building a 2nd listening port that mimics Ollama's evolving API is real scope + ongoing drift for a benefit mostly duplicated by `/v1` (the user's own OpenWhispr/OpenWebUI already use `/v1` directly). **Recommend defer/skip unless a concrete fleet client cannot be redirected to `/v1`.** (ii) **Upstream OSS PRs into Continue/Open WebUI** conflict with the locked "work scope = hal0 only." **Recommend out-of-scope** unless the user carves an explicit exception. (iii) **`hal0-recipes` standalone repo** conflicts with the §15.5 monorepo-over-separate-repo precedent; hal0 already has `bundles/`+`tiers/`. **Recommend folding recipe JSON into in-repo `bundles/` or `installer/manifests/`** unless the user specifically wants a public community-recipe repo.

**D4 — Host-tuning blast radius (§21.1).** Applying `amd_iommu=off` + reserving up to 120 GB as amdgpu GTT + `tuned-adm accelerator-performance` at the PVE-host level is NOT scoped to halo alone: `amd_iommu=off` changes device-isolation posture for every guest on that host, and the 120 GB GTT reservation permanently removes RAM from co-located services (langfuse CT105, TrueNAS-backed PBS, etc. per memory). **Needs explicit sign-off before hal0 ships an automated host-tuning script vs a manual opt-in runbook. Recommend defaulting to a documented manual runbook (never auto-run by any installer path)** unless the user confirms this Proxmox host is dedicated enough to halo to take the system-wide hit.

**D5 — Document-RAG as a first-class capability.** hal0 has no document-ingestion/vector-store RAG today — memory is Hindsight conversational memory only (single backend, locked). Adopting the "two-half RAG (ingest async / query sync)" workflow or the `services_any: qdrant OR weaviate OR chromadb` disjunction means deciding hal0 wants a document-RAG feature at all, and on what backend(s) — a new product-scope decision, not a gap-fill. **Recommend defer unless the user actively wants document-RAG.**

**D6 — Workflow catalog / generic DAG schema.** hal0 has zero generic workflow-graph concept (only a narrow ComfyUI prompt-graph translator). ODS's node-type vocab (trigger/http/llm/rag/store/output/schedule/stt/tts/agent) turns hal0 from "local inference server + admin dashboard" into a workflow-authoring platform — real scope expansion against the appliance/single-user philosophy. **Recommend documenting concrete pipelines (voice STT→LLM→TTS, memory) as fixed named recipes instead**, unless the user actively wants a DAG authoring surface.

**D7 — AI-CI automation cluster.** None of nightly-code-review / claude-review / ai-issue-triage / autonomous-code-scanner exists in `.github/workflows/` today (`hermes-sdk-diff.yml` is an unrelated SDK-drift bot the plan wants deleted). Adopting it means the repo's FIRST scheduled/unattended Claude surface, distinct from the interactive Opus-orchestrates/Sonnet-implements model. Tradeoff: high leverage for a Claude-heavy owner vs real cost ($/run caps), blast-radius (protected-paths, secret-scan, diff-cap), and a new guardrail doc + "operate autonomously, never AskUserQuestion, Let It Crash" prompt discipline that doesn't exist today. **Needs explicit yes/no + rollout order; if yes, recommend ai-issue-triage first (labels-only, zero blast radius).** Protected-paths should reuse the existing sunset-shim/`check-sunset` CI guardrail, not a parallel allow-list.

**D8 — Five-channel release model vs locked §7.7.** hal0 ships `main` + a working `nightly` channel + tags (`nightly.yml`, `hal0.release.channel`, `manifest.json` channel field) — live code. §7.7 explicitly says "remove nightly channel" to collapse to one scheme; ODS's 5-channel model implicitly argues to keep a nightly-equivalent. **Needs one explicit re-confirmation: keep §7.7's single-channel decision (then this candidate dies beyond its patch-triage-tree sub-idea, which lands regardless per §21.15), or reopen §7.7 and keep a nightly-equivalent.** Don't let this drift silently either way.

---

### 21 Revised sequencing

Slotting the above into the existing Phases 0–4 + model-layer epic (the plan's own §230-238 sequencing). Two items are urgent/high-ROI-early and should jump the queue:

- **KB-1 auth gap (§1) — urgent.** `/v1` is open on the LAN today. This blocks D2 (metrics auth), gates §21.9 `/v1/messages` (Claude Code will want a token), and is a prerequisite the network-exposure-policy CI test (§21.11) codifies. Pull forward into Phase 0/1.
- **§21.1 Strix-Halo host tuning — urgent, highest concrete ROI.** Independent of the code epics (it's a host-provision script on the halo box), gated only by Decision D4 sign-off. Land as soon as D4 is answered — it multiplies the value of every subsequent inference change and is a one-time cost. Pair its preflight WARN checks with the §21.4 doctor rework.

Otherwise:

- **Phase 0/1 (foundations):** KB-1 auth; §21.11 config contracts (network-exposure-policy CI, ports, golden-paths — cheap guardrails that protect later work); §21.15 PR template + triage tree (process, no code risk); §21.1 host-tune script + preflight WARNs (pending D4).
- **Model-layer epic (§7.1a/b + ML-1/2/3):** §21.7 managed-args denylist (rides §7.1a flag resolution); §21.2 gfx1151 arch-guard + `backend_versions.json` + cold-JIT timeouts + `--parallel` sweep (§7.1b registry + §20 bench); §21.6 backend-contract widening + auto-select + lifecycle state; §21.5 /v1/models extensions + tokenize + extra prefixes; §21.3 introspection endpoints (after ML-1 tables).
- **Phase 3 (slots/capacity):** §21.10 multi-model memory manager folds into the P3-slots `reaper.py` extraction (GTT-aware probe = elevate); §21.13 persona `timeout_s`/concurrency gates on the extracted capacity signal.
- **Phase 4 (release/process):** §21.15 receipt-schema + support-class table + "extend not build" framing; §21.4 doctor rework + support bundle (after §21.2 gfx guard supplies a diagnosis ID and §13 tables supply evidence); close the toolDefinitions drift-check.
- **After §19 (voice):** §21.9 `WS /v1/realtime` for OpenWhispr (needs the whisper.cpp GGUF slot); voice pipeline named-recipe doc.
- **Strategic, sequence after §21.5 /v1/models:** §21.9 `POST /v1/messages` + `hal0 launch claude` (Claude Code onboarding); §21.12 connect-clients docs + `setup-cursor`/`setup-continue` (with §17 Lane E); §21.14 `hal0 chat` REPL (self-contained, any time).
- **Decisions D1/D3/D5/D6/D7/D8** are gates, not scheduled work — resolve before their dependent items enter a phase. D2 and D4 gate Phase-0/1 items above and should be answered first.
---

### 21 Decisions — orchestrator dispositions (2026-07-18)

Autonomous calls (recommend + proceed; user may override on review):

- **D1 — DECIDED: keep local-first.** No silent cloud failover. IF fallback wanted later → option (b) only (opt-in per-persona `fallback_model`, explicit flag, off by default). Keep §7.2 in-process privilege helper (NOT a separate host-agent daemon); pull ODS hardening into it: per-service Lock, subprocess timeouts, 16KB body cap, default-deny bind, id-validation regex.
- **D2 — DECIDED: (i)** widen §7.1b `RUNNER_IMAGES` with `public_api_port`/`public_health_url`/`provider_url` — NO second `config/backends/*.json`. **(ii)** metrics-auth folds into the §1 (KB-1) auth lane — single PR owns `/api/metrics/prometheus` going from unauthenticated → §1 allow-list. Cross-lane note, not a fork.
- **D3 — DECIDED per locked scope.** Ollama-compat surface = DEFER/skip (OpenWhispr/OpenWebUI already use `/v1`). External OSS PRs into Continue/Open WebUI = OUT (work-scope=hal0-only). `hal0-recipes` = fold recipe JSON into in-repo `bundles/`, no standalone repo.
- **D5 — DECIDED: defer.** No document-RAG; memory stays Hindsight-only (single locked backend).
- **D6 — DECIDED: defer.** No generic workflow-DAG platform. Ship voice/memory as fixed named recipes.
- **D8 — HOLD existing §7.7.** Keep single-channel (remove nightly). Patch-triage-tree sub-idea lands regardless via §21.15.

Surfaced to user (genuine blockers, can't safely default): **D4** (host-tuning blast radius on shared PVE host), **D7** (AI-CI autonomous Claude surface — recurring $ + blast radius).

**D4 — RESOLVED (user, 2026-07-18): Manual runbook only.** §21.1 ships as a documented runbook + WARN-only preflight read-checks in `hal0 doctor`/`preflight.sh` (read `/sys/module/amdgpu/parameters/*`, `/proc/cmdline`, `sysctl`, `tuned-adm active` → single "host not tuned for Strix Halo" WARN). NEVER auto-run by any installer path. All numeric perf claims measure-first via `hal0-tune`.

**D7 — RESOLVED (user, 2026-07-18): ai-issue-triage first (labels-only).** Adopt triage bot only (zero write blast radius, minimal $) as the first unattended Claude surface. Prove out before review/scanner bots. Protected-paths reuse existing `check-sunset` guardrail, not a parallel allow-list. New guardrail doc + autonomous-prompt-discipline written when triage lands. Review/nightly/scanner = deferred pending triage results.

---

### 22. Settings rework (spec: hal0-specs/spec-settings.md)

Implementation-ready spec landed 2026-07-18. Core correction: the wireframe describes a Lemonade-shaped config plane (`/internal/config`, `hal0.env`, `recipe_options.json`) hal0 never built — hal0 already ships the thesis as `/api/settings` (pydantic `Hal0Config`=schema, `GET /schema` renders UI, `apply-plan` = the ⟳/⏻ 3-class registry in `_settings_apply.py`). Rework = adopt/extend `/api/settings` (E1 api.env `[server]` endpoint, E2 IA-over-typed-endpoints, E3 register every new key) + the P3-ui ESM split of `settings.jsx` (2598-line window-globals monolith) — SAME lane as tracker P3-ui, do once. ~40% buildable now, ~15% new keys, ~45% gated on §1(auth)/§21.1(tuning, D4 manual-runbook)/§21.4(doctor)/§21.10(memory)/§21.5+9(API)/§7.1(model config)/§13(telemetry). MVP cut: General/Loaded/Library/Health/Doctor(min)+shell-extraction first; Security+Hardware-Tuning visible-but-disabled until auth/tuning land. Full per-page→key map + risks in the spec.

---

## 23. Reconciliation ledger + cross-lane seam map (session 3, 2026-07-18)

Reconciles the master plan against the 12 banked implementation specs (`hal0-specs/spec-*.final.md`) +
the KB-1 auth surface map + spec-kb1-auth.md. Purpose per the "well-planned seams/bones, deeply
integrated" mandate: (1) resolve internal contradictions the deep specs surfaced, (2) make the
cross-lane seams explicit and single-owned, (3) encode the sequencing edges so lanes don't collide.
Where this section and an older section disagree, **§23 wins** (it is spec-verified).

### 23.1 In-plan contradictions resolved (authority ledger)

| # | Location (stale) | Corrected truth | Authority |
|---|---|---|---|
| C1 | §Phase2 #3 "device pure deletion, collapse 4 translators to 1, delete promote validators + `_cfg_effective_backend`" | Delete ONLY `device_to_legacy_backend` (Concept-A mirror). KEEP `canonical_device`/`device_to_backend`/`map_backend_to_device` (Concept-B runtime token) + `_cfg_effective_backend`. CONVERT promote validators to read-only shims (delete regresses legacy cpu/npu→gpu-rocm). 5 write sites + 2 fields. | P2-device CORRECTION block (already in-plan) + spec-p2-device.final. Fixed in §Phase2 #3 in place. |
| C2 | §7.4 "render one `config.yaml`" | Apply via `hermes config migrate` + `hermes config set` + `overrides.yaml` deep-merge. `config.yaml.j2` is deleted; whole-file render clobbers `image_gen.provider`/`tts.provider`. Jinja survives only for context files (SOUL/HERMES/AGENTS). | §18 gotcha + spec-hermes-provision.final / spec-plugin-suite.final. Fixed in §7.4 in place. |
| C3 | §7.4 + §18 table "hal0-memory 3 copies → 1" | 2 copies BY DESIGN (importable source + hyphen seed), byte-identical, parity-test-locked — KEEP BOTH. pi_coder TS reimpl already gone. Work = fix 2 stale docstrings + rename tools + add config schema. | §18.1 HP-1 CORRECTION + spec-plugin-suite.final. Fixed in §7.4 + §18 table in place. |
| C4 | §7.5 "SQLite deletes `SlotConfigStore` ChangeSet ~640 lines" vs P2-config "`SlotConfigStore` = THE single apply engine" | Not a delete: P2-config keeps `SlotConfigStore` as the single **slot-apply** engine; §7.5 SQLite **re-homes** its transaction machinery onto `BEGIN IMMEDIATE` and must not resurrect a 2nd slot-apply path (`stacks/apply` reconcile). | spec-p2-config.final + spec-ml1-sqlite.final. Fixed in §7.5 in place. |
| C5 | §Phase2 #1 + Phase-3 headline "delete the capabilities orchestrator apply path" | Too broad. `orchestrator.apply` STAYS (becomes slot-only write + create-on-select) — it is the one write path `stacks/apply.converge` calls. Delete only the orchestrator's in-apply **caps reconcile** + `stacks/apply`'s **parallel reconcile/duplicate `_CHILD_TO_*` map**. | spec-p2-config.final §. |
| C6 | §15.5/§15.6/§15.9 pip-package plugins + `hermes_agent.plugins` entry points + `pre_llm_call` hook + "ships 2 plugins" | Superseded by §18: uniform **dir-drop** via `_copy_plugin_tree` (no pip entry-point group for memory); memory API = `prefetch()`/`system_prompt_block()`/`sync_turn()`, **no `pre_llm_call` hook**. §15.2 separate-repo also dead (§15.5 monorepo → §18 dir-drop). Treat all §15.x packaging prose as historical; §18 is the live contract. | §18 header + spec-plugin-suite.final. (Prose left as decision-history; §18/§23 authoritative.) |

### 23.2 Cross-lane seam map (the load-bearing bones)

Each seam has ONE owner lane that defines it and N consumer lanes that bind to it. Build the owner
first or hand consumers a temporary local copy (merge-conflict surface). "two apply concepts" (S5a/S5b)
is the most-confused pair — they are **separate engines**, never unify them.

| ID | Seam / shared symbol | Owner lane | Consumers | Contract (do not drift) |
|---|---|---|---|---|
| S1 | `toolloop/engine.py::run_tool_loop(llm_fn, tools, dispatch_fn, *, max_rounds, on_event)` | P2-toolloop | P3-brain `service.py`, board_chat (→ thin alias), omni_router `run_loop` | Text-toolcall fallback + `_split_thinking` + `known_names` gating live INSIDE engine. `on_event` carries thinking(explicit+inline) + native+text tool calls + per-call result frames. The toolcall-leak fix, done once. |
| S2 | `config/store.py::store_root/assert_under_store/model_dir/file_dest/entry_pointer/mount_for/finalize_perms` | ML-3 (store) | P3-slots (`container._resolve_model_path` resolves via `by-id/<id>` + assert), providers container/kokoro/qwen3tts (mounts via `store.mount_for`) | ONE resolver read==write, precedence `HAL0_MODEL_STORE → effective_store() → paths.models_dir()`. `paths.model_store_root()` becomes a shim delegating here. |
| S3 | `by-id/<id>` store pointer indirection | ML-3 | P3-slots (slot config stores the **pointer**, not the rev-pinned path) | Slots survive revision bumps without editing slot TOMLs; atomic pointer flip = the set-swap. Direct parallel of §11.1 slot-`id` identity. |
| S4 | `ModelRegistry` §0.2 interface + 3 typed errors + `registry_write_lock`/`model_to_toml_dict`/`on_change` exports | ML-1 (`registry/store.py`, bind `ModelRegistry=SqliteModelRegistry`) | ~60 call sites, CLI `registry_commands.py`, API error-code middleware | Drop-in behind unchanged interface. `registry_write_lock`/`model_to_toml_dict` kept as **export shims** even though SQLite removes the flock (CLI imports them). `on_change` preserved (TOML-mirror trigger). |
| S5a | `SlotConfigStore` (apply/commit/transaction) — **slot-config** apply engine | P2-config | orchestrator.apply, stacks/apply.converge, every slot writer | Single writer of `slots/*.toml`. Governs SLOT config only. Re-homed onto SQLite by §7.5 (S-C4). |
| S5b | `_settings_apply.REGISTRY` + apply-plan (⟳/⏻ 3-class) — **settings** apply engine | §22 settings | `/api/settings`, KB-1 auth keys (E3), every new `Hal0Config`/`hal0.toml`/`api.env [server]` key | Governs `Hal0Config` reload/restart classing, NOT slot TOML. E1 adds `api.env [server]` endpoint. **Distinct from S5a — hal0 has TWO apply concepts; do not merge.** |
| S6 | `RUNNER_IMAGES` code registry (`hal0/runners/`) + `resolve_runner_image()` + `RunnerSupports{mtp,jinja,mmproj}` | §7.1b / ML-4 | §21.2 gfx-guard (`required_hip_archs`), §21.6 widen (`public_api_port/health_url/provider_url`), §20 bench (image_digest in cell_key), §13.3 `runner` column, §21.7 denylist | `runtime_family`=lookup not sniff. `resolve_runner_image` precedence env>manifest-digest>default unifies 3 chains (fixes FLM-ignores-`slot.image` + dead kokoro `image_ref`). Frozen code registry, NOT a DB table. |
| S7 | device Concept-B runtime token (`rocm|vulkan|cpu|flm`) via `device_to_backend`/`_cfg_effective_backend` | P2-device | SlotCard chip, NPU dispatch, argv assembly, `Slot.backend` API response | KEEP (device-derived). Only Concept-A mirror deleted. |
| S8 | `db/` foundation (`connection.py` `foreign_keys=ON`+`BEGIN IMMEDIATE`, forward-only `migrate.py`, `schema_migrations`) | ML-1 | ML-registry (`001_registry.sql`), ML-3 store (`002_store.sql` `store_blob`), §11.2 PortAuthority (`port_claim`), §13.3 metrics tables, §8.4 runtime tables | The one embedded-DB substrate. **Prerequisite for PortAuthority + metrics + runtime state, not just the registry.** |
| S9 | `security/exposure.py` route→AuthClass classification table | KB-1/§1 | auth-enforcement middleware, §21.11 network-exposure CI, §22 Security page, D2 metrics-auth | Single source of truth: same table drives runtime enforcement AND the CI ratchet AND the UI. Deny-by-default (unclassified→ADMIN). See §23.5. |
| S10 | `slots/capacity.CapacitySnapshot.free_vram_mb/total_vram_mb` (GTT-aware) | slots/capacity (exists) | §21.10 reaper GTT pressure probe (replaces raw `/proc/meminfo`) | The GTT-hidden-memory blind-spot fix. Reaper reads this, not `capacity._read_meminfo`. |
| S11 | `registry/fileset.py::SHARD_RE` (single source) + `plan_fileset`/`role_of`/`enumerate_repo`/`resolve_revision`/`runner_hint` | ML-2 | discover.py (imports shared `SHARD_RE` — stops deleting shards; **behavior flip**, flips test assertions), ML-4 (`runner_hint`→`preferred_runner`), update_check (set-wide) | discover's drop-shard and fileset's group-shard are the same regex; must share ONE definition. |
| S12 | §7.6 request seam (per-request measurement point) | §7.6 / §13 | §13.3 `request_metric` writes, Langfuse/OTLP export, reasoning-normalization | One measurement seam; never re-scatter. §13.3 `runner`/`device`/`modality` columns read the §7.1 model record + S6 + §7.1d modalities. |

### 23.3 Missing seam contracts to fold into the owning sections

**Model-store (§7.1e / ML-3):** (a) `assert_under_store` severity is SPLIT — **fail-fast on write/new-launch**, **warn (don't hard-fail) on an already-running slot resolve** (protect live slots); §430's "fail fast" is only half. (b) `store_blob(sha256 PK, refcount)` lives in a **separate `002_store.sql`** (ML-3), `model_file.sha256→store_blob.sha256` is the ref edge — refcount is NOT a column on `model_file`; §8.2 DDL must add it. (c) `_needs_pull` does a **pre-download blob-existence check → hardlink instead of stream** when the sha already exists (the real dedup mechanism, absent from §439). (d) NFS relabel: detect `statfs f_type==0x6969` and **OMIT `:z`/`:Z` entirely** + set `Mount.selinux=""` — §7.1e#7's "use `:Z`" is WRONG (`:Z` also relabels → chcon ENOTSUP on NFS). (e) derived-path layout **supersedes** the prefix-rewrite migration fix for new rows (legacy flat rows still need the rewrite).

**Runner-flags (§7.1a/b / ML-5):** (a) `--jinja` has no negation → it must be **conditionally injected** into a capability sub-segment gated on `runner.supports.jinja`, computed inside the shared `_resolve_llama_scalars` (else launch/preview drift — spec risk #6). (b) `ProfileConfig.image` required-validator (`schema.py:1345`) must be **relaxed to optional** before profiles can lose `image`; custom-profile round-trip needs a migration. (c) `STALE_RUNNER_IMAGE_REFS` (was `STALE_ROCMFPX_IMAGE_REFS`) moves to `runners/`, consumed by `updater.retag_stale_slot_images`.

**SQLite pilot (§8.2):** the §8.2 DDL is **illustrative, not complete** — it currently drops `capabilities/tags/name/size_bytes/quant/license/hf_filename/context_size/rope_freq_base`; the real schema must round-trip them losslessly. `model_file` lands **EMPTY** in ML-1 (ML-2 is first writer); import uses `INSERT OR IGNORE` (not REPLACE) for idempotency.

**Config (§Phase2 #1 / P2-config):** the migration window has 3 load-bearing pieces the plan omits — (a) `migrate_capabilities_into_slots()` one-shot boot fold (caps-wins-once); (b) **create-on-select** `_ensure_slot_exists` on ANY set-model/device (not just enable) — WITHOUT it, disabled-pre-pick slots (`embed-rerank`) live only in caps and are **silently dropped** at cutover; (c) switch `get_state`/`stacks/portable.snapshot` to derive.

**Brain (§7.3 / P3-brain):** (a) routing = `/api/brain/chat` **primary**, `/api/board/chat` becomes a **thin alias** (UI unchanged). (b) `tool_model="hal0/agent"` is correct (the always-on anchor, ADR-0023) — the `brain.toml` comment recommending `hal0/code` must change. (c) memory bank orphan: `private__hermes__hal0-brain` → `private__hal0-brain` needs rename/alias OR documented clean-cutover (viable on the fresh `halo` LXC). (d) board-decouple contract: kanban client **optional**, platform/admin/memory tools work with zero board dep — this operationalizes "core works without Hermes" for the brain. (e) `_validate_phase_graph` fails-fast: deleting brain phases requires fixing every `needs=`/`needs_previous=` edge or the provisioner import breaks. (f) keep the `hermes` half of `_phase_persona_seed`; delete only the hal0-brain half.

**Honcho removal (§1.2 / §5 / P1-honcho):** the plan says "migrate first, then delete" but omits the procedure. Contract: (a) command `hal0 memory migrate --from honcho --to hindsight --agent <id>` (dry-run→real), run while `hal0-honcho.service` + `cfg.honcho.*` + `honcho_migrate.py`/`MigrateState` **still exist**. (b) deletion ORDER: UI→CLI→API→provision→registry→(gate `grep honcho src/` empty)→delete the 2 modules→config schema→installer/units. Schema-first strands every `cfg.honcho.*` reader. (c) **per-private-workspace repeat** (each `agent_private=true` workspace = its own run). (d) dedup via `document_id=conclusion.id` + watermark `/var/lib/hal0/honcho/migrate-state.json`; snapshot it; NEVER re-run after Phase-1 delete. (e) **config-load boot risk**: live `hal0.toml` has persisted `[honcho] enabled=true`; verify `Hal0Config` tolerates/scrubs unknown tables after `HonchoConfig` removal or hal0-api won't boot (ship `_disable_honcho_hermes_host` scrub one release; keep `uninstall.sh` Honcho teardown for legacy boxes). (f) force `agent_providers[x]="honcho"` agents to Hindsight + re-provision before engine removal. (g) **unpinned decision:** `PgVectorProvider` is the **boot-degrade fallback** (in-memory serve when Hindsight unreachable), NOT dead — "collapse ABC→one concrete" as literally written hard-fails boot; decide keep-as-degrade (recommended) vs literal-collapse. (h) external dashboard repo consuming `/api/memory/honcho/stats`+`/provider` will 404 — sequence its change / decide `/provider` disposition.

**Bench (§20 / §20.1):** (a) baseline READ = in-process `SqliteModelRegistry` (§8.3), NOT the localhost `/api/models` HTTP hop (a bug). (b) argv-parity: resolved bench argv reuses `container.py resolve_argv` (S6 precedence) so plan==run==runtime. (c) write-back = **only** through the registry interface to the `model` table, gated by explicit confirm (`POST /api/benchmarks/apply`) — never a direct config path. (d) table ownership: extended `bench_run` lives in `/var/lib/hal0/hal0.db` as OBS T3 (delete the out-of-tree `bench.db` + `/var/lib/hal0-bench`); land bench columns with OBS-1. (e) `image_digest` (from S6 at plan-time) enters `cell_key` → rebuilt image re-benches exactly affected cells. (f) quiesce reads the live slot set from `/api/slots` scoped to the target device (kills the hardcoded `hal0-slot@{agent,brain,flm,rerank}` list); device env reuses `_gpu.gpu_visibility_env` as the single source, not a re-implemented `-dev ROCm0`.

### 23.4 Sequencing dependency edges (build DAG — additions the plan lacked)

```
S8 db/ foundation (connection+migrate+schema_migrations)  [ML-1, FIRST]
  ├─ 001_registry.sql (SqliteModelRegistry; model_file EMPTY, model.revision col)   [ML-1]
  │     └─BLOCKS→ ML-2 fileset (first writer of model_file)
  ├─ §11.2 PortAuthority (port_claim)          ← depends on db/ foundation (NEW edge)
  └─ §13.3 metrics tables + §8.4 runtime        ← depends on db/ foundation (§13.7 "after ML-1")

ML-2 fileset (S11 SHARD_RE, runner_hint) ─→ discover.py (shared SHARD_RE; intentional behavior flip)
                                          └─→ ML-4 (runner_hint → preferred_runner)
ML-3 store (config/store.py, 002_store.sql store_blob) ─→ P3-slots (_resolve_model_path via by-id + assert)

preferred_runner/mtp/jinja/architecture: land ONCE as pydantic fields (7.1a/b) → ML-1 maps to columns (never double-add)

ML-5 SEED_PROFILES shape-change (strip image/mtp/clones/--jinja) ─MUST PRECEDE→ P3-schema externalize SEED_PROFILES→share/*.toml
        (both edit the same dict = conflict; ML-5 shape first, P3-schema data-move second)

P2-toolloop (S1) ─HARD DEP→ P3-brain first-class (else brain carries a temp toolloop copy = _chat_stream conflict)
P3-perms (OwnershipStore default hal0 + drop-to-hal0 + privileged helper + hal0-api User=hal0) ─MUST PRECEDE→ §7.4 hermes installer slim (born-owned kills chown phases)
P3-brain phases leave hermes_provision.py ─MUST PRECEDE→ §7.4 slim to ~200 lines

§7.1d modalities + ML-4 runner ─PREREQ→ §13.3 request_metric.modality/runner columns
§13.3 tables + §21.2 gfx diagnosis ID ─PREREQ→ §21.4 doctor rework evidence

KB-1 auth (§1) ─GATES→ D2 metrics-auth, §21.9 /v1/messages token, §21.11 exposure-CI, §22 Security page
P2-config migration window (3 ordered releases): [N] land migrator + create-on-select → [N boot] switch to derive + stop writing caps (cutover = 1 restart) → [N+1] delete caps readers/CLI/perms-row
```

### 23.5 First-class KB-1 / §1 authentication architecture (spec: hal0-specs/spec-kb1-auth.md)

The plan referenced "§1 auth" as a lane from §14.1 (board), D2 (metrics), §21.11 (exposure-CI), §22
(Security page) but never defined the auth architecture. Grounded in the full attack-surface map:
**~40 mutating/RCE-class routers + `/v1` are unauthenticated on `0.0.0.0:8080` today** (installer, slots,
models-pull, updater, secrets, board/chat agentic loop, …) — LAN-RCE, not just the board. The one
existing seam (`agents/_auth.py` HMAC cookie + origin) protects only chat_proxy at 5 imperative sites.

**Architecture — 3-tier, deny-by-default, single classification source:**
- **Credentials:** `HAL0_ADMIN_KEY` (full incl. RCE/config/secrets/updater/installer) · `HAL0_CLIENT_KEY`
  (inference + read-only introspection) · browser HMAC session cookie (reuse `agents/_auth.py`) =
  admin-equivalent, minted by `POST /api/auth/login`. Presented via cookie / `Authorization: Bearer` /
  `?api_key=` (WS/SSE, since browsers can't header WS upgrades).
- **Enforcement:** pure-ASGI middleware at the app factory (after `log_scrub.install`, before routers) —
  NOT per-route Depends (60+ routers, one miss = a hole). Handles `http` + `websocket` scopes (SSE
  covered as HTTP GET; WS rejected pre-accept). Classifies by `security/exposure.py` (S9): **OPEN**
  (tiny explicit allowlist: `GET /v1/models`, `/api/metrics/prometheus`, `/api/health`, `/api/config/urls`,
  static SPA, auth login/status) · **CLIENT** (`/v1/*` inference + RO GETs) · **ADMIN** (all mutating).
  **Unclassified → ADMIN** (deny-by-default ratchet: a new route is locked until classified). Installer
  prefix = **BOOTSTRAP** (open iff no admin key set yet, else ADMIN) — closes the first-run chicken-egg
  without leaving RCE routers open during bootstrap.
- **Posture (test-safe):** `HAL0_REQUIRE_AUTH` env; default derived — **enforce when bind non-loopback
  OR any key set**; loopback+no-keys = dev-open (the 700+ TestClient suite stays green). The fresh `halo`
  LXC is LAN-bound → enforced (installer seeds keys into `/etc/hal0/api.env`); lxc105 untouched.
- **§21.11 exposure-CI (ships with it):** `tests/security/test_exposure.py` walks `create_app().routes`,
  fails on any unclassified route AND if the OPEN set widens — the same table (S9) drives enforcement,
  CI, and the §22 Security page. Resolves D2 (metrics-auth) in one lane.

**Shippable steps (each green+pushed):** (1) `security/exposure.py` + exposure CI (guardrail, no behavior
change). (2) `api/auth.py` principal/key/posture. (3) wire middleware + `POST /api/auth/login` +
`GET /api/auth/status`, flip CI to enforce. (4) WS/SSE `?api_key=` coverage (board events, logs, activity,
events, journal, approvals, benchmarks, mcp). (5) §22 Security page (P3-ui) reads/writes keys via E1 api.env.
Compose with §14.1 (board default `read_only=True` — an independent second `read_only` trigger alongside
§7.3 readiness-degrade, both reusing the `_is_read_tool` gate) and §21.13 persona `safe/dangerous_commands`.

---

## 24. Execution waves / rollout program (session 3, 2026-07-18)

Turns the §23.4 build-DAG + all lanes into an ordered, collision-aware program. Model: this Opus
session (or its successor) is orchestrator/reviewer; **MiniMax workers do read-only/summarizing +
spec-authoring**, **Sonnet worktree agents do bulk implementation**, Opus reviews + merges each lane
to `rework/descar`, keeps `check-sunset` green, pushes, watches CI. Cap ~3–4 file-mutating lanes per
wave; lanes in the same **collision class** (shared file territory) are serialized, not parallelized.

### 24.1 Collision classes (file territories — never run two mutating lanes in the same class concurrently)
- **API**: `src/hal0/api/**` (KB-1 auth middleware, P3-brain lifespan, P3-routers, §21.3/21.5/21.9 endpoints)
- **SLOTS**: `src/hal0/slots/**` (P3-slots, §11.1 id-keying, §11.2 PortAuthority)
- **SCHEMA**: `src/hal0/config/schema.py` (P3-schema A/B/C/D, P2-device, P2-config, §7.1d taxonomy)
- **STORE/DB**: `src/hal0/config/store.py`, `registry/**`, `db/**` (ML-1/2/3)
- **RUNNERS**: `src/hal0/runners/**` + flag resolution (ML-4/5, §21.2/21.7)
- **INSTALL**: `install/**`, `installer/**`, `cli/setup*` (§17, P3-perms, P3-quadlet, hermes-provision)
- **BRAIN**: `src/hal0/brain/**` (P3-brain)
- **UI**: `ui/**` (P3-ui settings, §7.1d UI churn)
- **BENCH**: `src/hal0/bench/**` (§20)
- **PROCESS**: `.github/**`, `docs/**`, `tests/*markers` (§21.11/21.15, Phase 4)

### 24.2 Wave schedule

| Wave | Lanes (collision class) | Deps satisfied | Spec |
|---|---|---|---|
| **W1 (IN FLIGHT)** | KB-1 auth [API] · P3-slots+§21.10 [SLOTS] · P3-schema Part A [SCHEMA] | none unlanded | ✅ all spec'd |
| **W2** | P2-device [SCHEMA] · ML-1 db-foundation+SqliteModelRegistry pilot [STORE/DB] · §21.15 PR template [PROCESS] | W1 P3-schema-A merged (schema.py reduced) before P2-device | ✅ p2-device, ml1-sqlite spec'd; §21.15 trivial |
| **W3** | P3-schema Part B SlotConfig split [SCHEMA] · ML-2 fileset [STORE/DB] · P2-config truth-collapse [SCHEMA-adjacent loader] · P3-perms [INSTALL] | P2-device landed (Part B); ML-1 landed (ML-2); **P3-perms spec NEEDS AUTHORING** | ⚠ P3-perms spec pending |
| **W4** | ML-3 store [STORE/DB] · §7.1d taxonomy/ML-6 [SCHEMA+API+UI — big] · P3-brain [BRAIN+API] · P3-quadlet [INSTALL] | ML-2 landed; toolloop done; **§7.1d + P3-quadlet specs NEED AUTHORING** | ⚠ p3-brain spec'd; §7.1d + quadlet pending |
| **W5** | ML-4 runner-registry + ML-5 flags [RUNNERS] · §21.2 gfx-guard + §21.7 managed-args [RUNNERS] · P2-memory/honcho removal [migration] | ML-2 runner_hint; §21.10/device landed | ✅ ml-runner-flags spec'd; honcho procedure in §23.3 |
| **W6** | §13 OBS metrics core [STORE/DB+API] · §20 bench rework [BENCH] · §21.3/21.5 introspection+/v1/models ext [API] · P3-routers [API] | ML-1 tables; **OBS + P3-routers specs NEED AUTHORING** | ⚠ bench spec'd; OBS + routers pending |
| **W7** | P3-ui settings §22 [UI] · §17 installer overhaul [INSTALL] · P3-hermes slim + P3-hermes-mem [INSTALL] · P3-runtime-db [STORE/DB] | §7.1d taxonomy + KB-1 landed (settings); P3-perms landed (hermes/installer); ML-1 (runtime-db) | ⚠ settings spec'd; §17 edit-plan pending |
| **W8 (Phase 4)** | test tiers/markers · docs collapse · §21.4 doctor + bundle · §21.9 /v1/messages + realtime (after §19 voice) · §21.12 client docs · §21.14 hal0 chat | prior waves; §19 voice slot for realtime | ⚠ voice + doctor edit-plans pending |

**Cross-wave riders (fold into host lane, not separate):** §21.7 managed-args→ML-5 · §21.2→§7.1b/ML-4 · §21.10→P3-slots (W1) · §21.11 exposure-CI→KB-1 (W1) · §21.13 persona-hardening→P3-brain+§14.1 · §21.6 backend-widen→ML-4 · D2 metrics-auth→KB-1.

### 24.3 Spec-authoring backlog (author BEFORE the lane's wave — route to MiniMax read-only workers)
| Spec | Gates wave | Priority | Source material |
|---|---|---|---|
| **P3-perms** (OwnershipStore adoption, drop-to-hal0, one privileged helper, hal0-api User=hal0) | W3 | HIGH (gates hermes/quadlet/installer) | §7.2 + §17 + `install/perms.py` |
| **§11.1 slot id-keying + §11.2 PortAuthority** | W3/W4 (slots-final, ports) | HIGH | §11 + harvest `feat/brain-tool-use-hardening` port-claim registry |
| **OBS/§13 metrics core** (db tables, request seam wiring, aggregator, native Performance view, read API §21.3) | W6 | HIGH (gates bench, §21.3) | §13 + §7.6 request seam |
| **§7.1d taxonomy/ML-6** (Modality/ModelCapabilities/tags, kill labels routing gate, UI churn) | W4 | HIGH (cross-cutting) | §7.1d + omni_router/filter + providers/flm + UI |
| **P3-quadlet** (`.container` units, delete hand-rendered unit strings) | W4 | MED | §7.2 + `providers/container.py` |
| **P3-routers** (thin models.py/slots.py→service modules, Pydantic bodies) | W6 | MED | §Phase3.4 |
| **§17 installer edit-plan** (thin shell + thick Python provisioner, one profile authority) | W7 | MED | §17 (design done, needs edit-plan) |
| **§21.4 doctor + bundle** edit-plan / **§19 voice slot** / **§21.9 realtime** | W8 | LOW | §21.4 / §19 / §21.9 |

### 24.4 Migration-window lanes (need LIVE steps before code deletion — orchestrator-run, not agent)
- **P2-memory/honcho removal (W5):** run the §23.3 procedure — `hal0 memory migrate --from honcho --to hindsight` per workspace on the source box FIRST, verify, snapshot migrate-state, THEN the ordered deletion (UI→CLI→API→provision→registry→modules→schema→units). Verify `Hal0Config` tolerates persisted `[honcho]` table or hal0-api won't boot. Decide `PgVectorProvider` keep-as-degrade (recommended) vs collapse.
- **P2-config truth-collapse (W3):** 3-release window (migrator+create-on-select → derive-cutover → delete readers). Create-on-select is load-bearing (else silent pick loss).
- **P2-device (W2):** shim-not-delete edit order (§23.3).
- **Deploy:** fresh `halo` LXC side-by-side (§12), migrate data, validate, cut over. lxc105 untouched.

### 24.5 Definition of done (every lane)
Merged to `rework/descar` · `check-sunset` green + scar baseline ↓/neutral · public names kept as
delegators/re-exports (zero caller migration) · targeted tests + import-smoke pass · CI green on push ·
tracker row flipped + changelog line · surface-impacts (`hal0-rework-surface-impacts.md`) addressed or
deferred · the lane's cross-lane seam (§23.2) left intact for consumers.
