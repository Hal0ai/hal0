# Handoff — fold rework→main merge/meld/fix follow-ups into the R5 docs

> **Paste-in prompt for the follow-up session.** You are holding a list of
> merge / meld / fix suggestions produced while landing `rework/descar` onto
> GitHub `main`. This brief tells you exactly WHERE each kind of finding
> goes, in WHAT format, without clobbering the conventions these docs
> already enforce. Read `docs/rework/REWORK_BOARD_PROTOCOL.md` first
> (single-writer rule, lane lifecycle, verify/merge discipline).

## Base facts (verify before editing)
- `rework/descar` is in sync with `origin/rework/descar` (tip moves; re-check).
- GitHub `main` has advanced past descar's branch base: `462b28f` + #1315
  (merged) + #1316 (UI-API-1 managed-arg gap CLOSED — launch + create +
  `/validate`). Any descar→main landing rebases onto **current** `origin/main`.
- The R5 docs lanes P4-docs / P4-rules / §21.11 golden-paths are ✔ on descar.

## The doc surfaces you may write to (and their ONE job each)

| # | file | its single job | how to add |
|---|------|----------------|------------|
| 1 | `docs/rework/REWORK_BOARD.md` | **status** layer: lane rows + open-adds | **single-writer** — see below |
| 2 | `ARCHITECTURE.md` | the one authoritative internal doc: architecture, glossary, standing decisions **inline** (no ADR tree) | edit the relevant section inline |
| 3 | `CONTRIBUTING.md` → *Anti-scar rules* | the durable rules that stop scar regrowth (currently 1–11, some **gated**) | append rule 12+ only for a genuinely new, earned lesson |
| 4 | `tests/golden_paths/__init__.py` | authoritative 15-scenario coverage map (CI-here / existing-suite / deploy-runbook) | update the map row + cite the owner |
| 5 | `docs/rework/golden-paths-halo143-runbook.md` | deploy-only golden-path steps, both-boxes policy | add/adjust the scripted step |

## Routing table — finding type → target

Classify each of your suggestions, then apply the row:

- **A cutover / migration / deploy step** (something that must happen when
  descar lands on main, or on a live box) → **board** Migration-window lane
  row *or* a `## Open review-driven adds` bullet, **and** if it's a real-box
  action, a step in the **halo143 runbook** (#5). Don't bury a deploy action
  only in prose — it needs a checkable step.

- **A code meld / reconciliation** (two implementations to unify when the
  branches merge; a conflict resolution like the AGENTS.md case below) →
  **board** row for the owning lane (status ▶/☐, note the meld), *and* if it
  changes a standing decision, update **ARCHITECTURE.md** inline.

- **A fix follow-up** (a bug or hardening the merge surfaced) → **board** row
  under the owning lane or `## Open review-driven adds`, with file:line
  evidence and the fix sketch. If it's already a PR on main, point the row
  AT the PR number (like UI-API-1 → #1316) — do NOT re-spec a shipped fix.

- **A new anti-scar lesson** (a class of mistake the meld revealed, ideally
  one that bit more than once) → **CONTRIBUTING.md** rule 12+. One line rule
  + one line why. Mark **(gated)** only if CI actually enforces it. Do not
  add aspirational rules with no teeth.

- **An architecture / decision change** → **ARCHITECTURE.md** inline, next to
  the code it governs. hal0 keeps **no ADR tree** (rule 9: no ghost-doc
  citations) — do not create `docs/design/adr-NNN`; the rationale lives with
  the statement.

- **A golden-path gap** (a scenario the merge shows is unverified) → update
  the map in **`tests/golden_paths/__init__.py`**: assign it a mechanism
  (CI-here / existing-suite / deploy-runbook) and cite the owner. If
  CI-automatable, add an interface-level test (public routes only, survives
  the SLOT-B rewrite) — but FIRST grep for an existing owner (rule 11); a
  duplicate is a scar.

## Hard conventions — do not violate

1. **Board single-writer.** The board has one writer (the orchestrator
   session). If you are NOT it: put your board changes as **row deltas** in
   your report (lane id → new status/note) and let the writer apply them, or
   request the writer token. If you ARE it: edit directly, ride the change on
   a merge push.
2. **One owner per fact** (anti-scar rule 1). Before adding a test/row/section
   for a fact, grep for an existing owner and extend it instead of forking.
3. **No ghost-doc citations** (rule 9). Every path/PR/file you cite must exist
   in the tree or on the remote. The docs-reference ratchet will fail on a
   dangling link.
4. **Status legend** (board): `✔ done+CI-green · ▶ in-flight · ☐ todo ·
   ⏸ deferred · ⛔ blocked`. A row is ✔ only with merge SHA + verify evidence.
5. **Deploy-affecting = both boxes.** 150 (4.9.3/privileged) + 143
   (5.7/unprivileged). Record per box.

## Known open item to fold in (already surfaced)
- ⚠ **Root `AGENTS.md` resurrection.** The P4-docs collapse deleted
  `CONTEXT.md` + `AGENTS.md` (content already inline in `ARCHITECTURE.md`).
  Post-reconciliation, `CONTEXT.md` stayed gone but **`AGENTS.md` reappeared**
  — likely a rebase kept the remote side. Decide: re-delete it (finish the
  collapse) or keep it as a thin pointer stub. Record the decision on the
  board P4-docs row and, if kept, make it a pointer to
  `ARCHITECTURE.md#bundled-agents-v03` — not a second copy of the content.

## Output
For each suggestion: `finding → target file → exact edit (or board row delta)`.
Apply the file edits directly; hand board deltas to the writer if you don't
hold the token. Run the capped gate (ruff + format-check + import smoke +
sunset + named tests) on any code/test touch; docs-only touches just need the
dangling-link grep. Report merge SHAs + board deltas back.

---

## Graphify structural findings (2026-07-19) — routed

Source: fresh graphify graph on `rework/descar` HEAD `270a35ae` — 26,181 nodes /
49,958 edges / 1,179 communities, code AST-only (0 import cycles, 88% EXTRACTED).
6-worker swarm analysis; per-slice reports at `graphify-out/analysis/w1..w6-*.md`
(graph.json + analysis/ are gitignored per the PR-churn rule — reports are the
durable artifact if you want to commit them under `docs/rework/`). Every LOC/count
below re-verified against the tree (`wc -l` / `grep -c`), not just graph degree.
Board changes are **deltas** (not the writer this session) — hand to the writer.

### → ARCHITECTURE.md (inline decision) + board (P3-routers lane)

- **F1. `api/__init__.py` is the router god the P3-routers spec never listed.**
  `src/hal0/api/__init__.py` = **1,892 LOC** (larger than any single route module),
  `create_app()` degree 104, **50** `app.include_router(...)` mounts (L1441–L1686).
  `src/hal0/api/routes/__init__.py` is **0 bytes** — no mount registry.
  → **ARCHITECTURE.md**: record the standing decision "the API app-composer and the
  route-mount registry are separate concerns" inline next to the API section.
  → **board delta**: `P3-routers` → note *scope gap* — spec enumerates
  models/slots/mcp/chat_templates/exposure but omits `api/__init__.py` (composer),
  `routes/memory.py` (1,205 LOC, 14 handlers), `routes/memory_admin.py`. Add file 1.0
  = split `api/app.py` (FastAPI+middleware+lifespan) from the 50-line mount registry
  seeded into the empty `routes/__init__.py`. **Kills the degree-104 god.** Not a
  shipped PR — needs a lane, do not re-spec as done.

### → board `## Open review-driven adds` (new P3-providers lane candidate)

- **F2. `providers/container.py` (1,967 LOC) has SlotManager's 8-responsibility
  fingerprint and NO spec covers it.** Degree 113, fuses Quadlet render +
  mount derivation + launch-plan dispatch + NPU-trio classify + ctx-window derive.
  → **board delta**: add `P3-providers` (☐) — mirror P3-slots. Split
  `container/launch.py` (spawn/terminate/probe) + `container/spec_render.py`
  (quadlet text + flag resolution), keep `ContainerProvider` as thin facade.
  First extraction = `_resolve_llama_scalars` subtree (already half-contained).

- **F3. `_PROVIDERS` registry is decorative — silent-misroute risk (fix
  follow-up).** `src/hal0/providers/__init__.py` registers only container/flm/comfyui;
  authoritative dispatch `_spec_provider_for()` (`src/hal0/providers/container.py`)
  hardcodes all 5 families (Kokoro/Qwen3TTS instantiated fresh, never via
  `get_provider`). Two dispatch surfaces that can drift.
  → **board delta**: `ML-5`/providers lane row — "make `_PROVIDERS` the single
  dispatch truth; `_spec_provider_for` returns `get_provider(family)`."
  file:line evidence in `graphify-out/analysis/w2-backend-services.md` F1/R1.

- **F4. Provider-layer simplifications (code-meld, low risk).**
  `FLMProvider` fuses launcher+HTTP-client while `LlamaServerProvider` shows the
  clean split; `CapabilityOrchestrator` holds 6 parallel child-mapping dicts →
  one `CapabilityChildSpec` table; 3 distinct `MemoryProvider` classes share the
  name across `src/hal0/memory/provider.py`, the Hermes plugin, and a test fixture.
  → **board delta**: single `## Open review-driven adds` bullet pointing at
  `w2-backend-services.md` Rec1/Rec2/Rec5/Rec6. Not urgent; bundle behind FLAGS-own.

### → board (frontend lane) — no golden-path change (these are UI unit gaps)

- **F5. Frontend runs two parallel API seams.** Real ESM stack
  (`ui/src/api/client.ts` `apiGet` → `endpoints.ts`) coexists with a legacy
  `window.__hal0Use*` hook-bridge layer for the in-browser-Babel prototype panes
  (`ui/src/main.tsx`). Plus `ui/src/api/mock.ts` = **1,233 LOC** parallel API
  (32 allowlist rows); builder↔backend drift invisible until exercised.
  `ui/src/api/hooks/useBoard.ts` = **1,494 LOC** (REST+mutations+WS+SSE in one).
  → **board delta**: UI lane row — "split `useBoard.ts` along transport
  boundaries; add `mockFetch` contract test asserting every `ENDPOINTS` key has a
  builder; migrate bridge panes to ESM." UI test ratio ~22% (9 files / 40+ hooks).

### → tests/golden_paths + board (test-coverage gaps)

- **F6. `v1.py` OpenAI-compat routes have no direct test (coverage gap).**
  `src/hal0/api/routes/v1.py` (1,685 LOC, degree 42) — `images_generations`,
  `audio_speech`, `audio_transcriptions`, `embeddings`, `_dispatch_via_npu_trio`
  exercised only transitively (board_chat / chat_normalization). Also
  `StackApplyEngine` (zero test neighbors), `UpstreamRegistry`.
  → **golden-paths**: this is unit-level, not deploy-shaped — do NOT add to the
  15-scenario map. **board delta**: coverage row — "add `tests/api/test_v1_routes.py`
  (grep for existing owner FIRST, rule 11)."

### → CONTRIBUTING.md anti-scar rule 12 (bit twice — earns a rule)

- **F7. A wire contract that only exists on the live peer must be pinned by a
  contract fixture — CI-green ≠ deploy-green.** HP-executor merged ✔ but the board
  flags `WORKER_BASE_PATH /api/plugins/kanban/runs` as *unpinned by contract
  fixtures*. Same class as the historical route-collision scar (`43f29e30` landed,
  duplicate `0b93a48b` passed CI then broke on newer FastAPI). Two hits = a rule.
  → **CONTRIBUTING.md**: append **Rule 12** (non-gated — no CI teeth yet, mark so):
  *"Any contract against a live peer (Hermes plugin path, external route shape) is
  pinned by a fixture under `tests/fixtures/hermes/contracts/` before the lane is ✔.
  CI-green is not deploy-green — the route-collision (`43f29e30`) and HP-executor
  `WORKER_BASE_PATH` both proved it."*
  → **board delta** + **halo143 runbook (#5)**: HP-executor row — add a
  **both-boxes** deploy step "validate `WORKER_BASE_PATH` against live Hermes on
  150 (privileged) + 143 (unprivileged)" as a checkable runbook line, not prose.

### → board (sequencing note, FLAGS-own critical path)

- **F8. FLAGS-own is the standing critical path.** Its spec node
  (`docs/rework/hal0-specs/spec-flags-ownership.md`) is graph-degree-1 (orphan);
  `slots/manager.py` still holds flag-bearing fields (device/chat_template on slot).
  It blocks slot purity AND P2-config's "one apply engine."
  → **board delta**: `FLAGS-own` row — note it need not wait on P3-routers inc-3 +
  P3-runtime-db; dispatch a narrow **increment-A** (model device/chat_template moves
  only) to unblock. Sequencing decision for the writer.

**Capped-gate note:** every item above is docs-only or a board delta as written —
no code touched here, so only the dangling-link grep applies. The moment a
follow-up applies F1/F2/F3/F6 as code, run the full capped gate (ruff +
format-check + import smoke + sunset + named tests).
