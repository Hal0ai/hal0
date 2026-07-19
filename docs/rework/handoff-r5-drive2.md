# Handoff — R5 drive 2: put it back together (paste-in prompt, Fable orchestrator)

> **You are the R5 drive-2 orchestrator.** Phase 0 (correctness & security) is DONE and
> merged. Your mission is the rest: wire every surface back into sync, finish the structural
> decomposition, make the two most-touched UX flows seamless, hunt down everything
> overlooked — dead code, stubs, dead settings, filler strings, stale names — and land it
> all in a state **worthy of a new release**. This drive is where the rework's payoff
> becomes visible; the bar is "better than ever", not "done enough".
>
> **This is a ONE-session orchestration** (decision recorded §8): the board is
> single-writer and FLAGS-own spans backend+UI, so a second session would fight over the
> same seam. You get parallelism from **agent teams in isolated worktrees**, not from
> parallel sessions. You ARE the board writer for this drive.
>
> Read in order: (1) this handoff · (2) `docs/rework/r5-sync-assessment-2026-07-19.md`
> (the evidence base — §-refs below point into it; **re-verify `path:line` cites before
> acting — Phase 0 moved code**) · (3) `docs/rework/REWORK_BOARD_PROTOCOL.md` ·
> (4) `docs/rework/REWORK_BOARD.md` (live status) · (5) `docs/rework/REWORK.md` (finish
> line + DoD). Specs: `docs/rework/hal0-specs/`. Supersedes `handoff-r5-endgame.md`
> (its Phase 0 is done; its Phases 1–4 are refined here).

---

## 1. Base facts (verify before anything — tips move)

- **Phase 0 merged to `rework/descar` @ `278b32a8`** (tip has already moved past — e.g.
  `68181bc` e2e fix riding the mock-gate change; re-fetch): SEC-mcp-clientid `5d7cd283` ·
  MCP-patch (+ `_REST_MAP` route-sync pin) `b878ff9d` · CLI-auth (4 bypass sites + authed
  stream helper) `d55aa36d` · mock-prod-gate (method+env gate + lazy fixtures) `54ccd580` ·
  INSTALL-target (hal0.target + model-store PermRow + uninstaller sync) `1bb9bf70` ·
  SEC-hermes-mcp-cred `1c01dbab`. Consolidated gate: 888 passed / 1 skipped / 1
  pre-existing env fail (rocminfo). Board updated on `278b32a8`. All Phase-0 worktrees and
  team branches torn down — **re-create scaffolding fresh**.
- **The assessment + graph** (`r5-sync-assessment-2026-07-19.md`, `handoff-r5-endgame.md`,
  `graphify-out/` report+wiki) ride PR **#1317** → `main`; verify whether it has merged and
  whether the docs need a descar cherry-pick so your tree has them.
- **NOT started:** Phase 1 (sync wiring) and everything after. Any scaffolding you find
  referenced in old notes is gone.
- `main` carries #1315 + #1316 (`POST /api/models/{id}/validate`); descar→main landings
  rebase onto current `origin/main`.
- Work lands on **`rework/descar`**; it merges to main at phase boundaries.

## 2. Orchestration model — max parallelism, total git/tracking discipline

**Team topology.** Dispatch lanes as parallel agent teams, each in its **own git worktree +
branch** off current descar. Parallelize **across** the board's collision classes
(SEC · MODEL · RUNNER · SLOT · INSTALL · HERMES · API · UI · OBS · DOCS · DEPLOY);
**serialize within** a class. Typical steady state: 3–6 teams live at once. You (Fable)
never build in-lane while teams are running — you dispatch, review, reconcile, merge.

**Model tiering (mandated):**
- **Haiku** — mechanical fan-out: grep inventories (string hunts, dead-export sweeps,
  ENDPOINTS consolidation), docs application from a precise brief, fixture realignment.
- **Sonnet** — standard lane builds, research/surveys, e2e authoring, board-delta drafting.
- **Opus** — the hard code tasks: pull-orchestration extraction, `lifespan()` phase-split,
  route-map autogen, FLAGS-own backend migration.
- **Fable (you)** — every merge review ("X-built, Fable-reviewed + independently re-run"
  per board discipline), adversarial verification of risky claims, cross-lane
  reconciliation (§4b class), user-decision surfacing, board writes.

**Git discipline (total management):**
1. One lane = one worktree = one branch; lane commits stay small and typed
   (`feat|fix|docs|refactor(scope): …`).
2. Merge to descar ONLY after: Fable review + independently re-run **capped gate**
   (`ruff check` + `format --check` + import smoke + sunset + named pytest targets; UI:
   tsc + eslint + build + targeted γ). Board row gets status + merge SHA in the same push.
3. Consolidated gate (full python + γ) before each **push wave**; push waves batch 2–4 lane
   merges. CI red on descar = stop-the-line: fix forward before any new merge.
4. Tear down each worktree + team branch immediately after its merge (Phase-0 pattern).
5. Never rewrite descar history; fix forward.

**Tracking.** Mirror every dispatched lane as a Task (TaskCreate → in_progress at dispatch
→ completed at merge). The board stays canonical; tasks are your live cockpit. Keep a
phase-level task open per §5 phase; close it only when the phase's DoD bullet list is met.

## 3. NEW defect lanes (user-reported, live) — with starting evidence

These are user-visible regressions/rough edges observed on live boxes. Ground truth beats
the notes below — repro first, then fix at the root.

- **VERS-flash — old version number flashes before the real one loads.**
  `ui/index.html:15,18` hardcodes `v0.5.0-alpha.1` in `<title>` + meta (matches
  `ui/package.json:4`), and the O24 investigation used exactly this literal as the
  stale-dist tell. Fix: build-time inject (Vite define from package.json) + set
  `document.title` from `useUpdateState` after mount; then **sweep for every other
  hardcoded version literal** in ui/src + src/hal0 (AboutPage was already converted —
  same class). Assessment §1.3.
- **NAMES-stale — slots named `primary`/`legacy` appear during reloads.** Verified sources
  on descar: `ui/src/dash/data.jsx:64,88` (fixture slots literally named "primary" and
  "legacy"), `ui/src/api/mockFixtures.ts:163,179,281,294` (same), `useSlots.ts:204`
  (heuristic keyed on `name === 'primary'`), `agents-overview.jsx:144` +
  `dashboard-redesign.jsx:172` (`slots.find(name === 'primary')` fallback selectors).
  Phase 0's mock-prod-gate closed the *fetch-fallback* path — but if fixtures seed
  **initial React state** (hydration-window flash) or the name-keyed heuristics pick a
  fallback label, the gate doesn't cover it. Lane: repro the reload flash → trace which
  path renders fixture names → replace name-literal heuristics with slot *type/role* keys
  → ensure loading states render skeletons, never fixture rows. Add an e2e that asserts no
  fixture slot name ever renders against a live-shaped mock.
- **DRAWER-shape — the two most-touched surfaces must reflect the new ownership model.**
  This is the FLAGS-own UI half, elevated to a first-class UX goal:
  - **Slot edit drawer → the new SIMPLE shape**: slot = id/name/model/port/state
    (spec-flags-ownership §7 slot purity). No flag/device/template surface. Editing a slot
    should feel like renaming a socket, not tuning an engine.
  - **Model edit drawer → the new COMPLEX shape**: flags, device, chat_template,
    capabilities, runner, defaults all live on the model record (ModelDrawer's
    copy-on-stamp editor from D1 is the seed — it becomes the full home).
  - **Staff these with UI/UX-specialized agents** and hold a real bar: loading/error/empty
    states, optimistic updates with rollback, keyboard flow, no dead controls, no
    disabled-with-reason left where the backend now exists. These are the two surfaces
    operators touch most — SEAMLESS is the acceptance criterion, reviewed by you against
    the running app (`/run` + screenshots), not just specs passing.

## 4. The SWEEP — overlooked, forgotten, dead (standing workstream)

A dedicated parallel workstream, running all drive: find what everyone stopped seeing.
Method: **Haiku fan-out inventories → Sonnet triage (dead vs load-bearing, with evidence)
→ Fable adjudicates deletions → lanes execute**. Every deletion lowers the scar baseline;
every keep gets a `HAL0-SUNSET` stamp or an owner. Inventory classes:

1. **Dead code**: unreferenced exports/functions/modules (both trees), unreachable routes,
   dead client hooks (`useCapability`/`useCapabilityPatch` class — §2), dead scaffolding
   (`NOT_IMPLEMENTED` constant class — §5), orphaned CSS files/rules (4 eras, 6,247 lines
   — §1.3), dead re-export shims after the importer flips.
2. **Stubs & placeholders**: "not yet wired — placeholder" pages (§1.2), disabled-with-
   reason controls whose backend now exists, 501 endpoints advertised in UI (`/api/mcp`
   lifecycle — §4.5), fake data (hardcoded MCP catalog stars — §4.5).
3. **Dead settings**: config keys nothing reads (grep every `Hal0Config` leaf against
   consumers), UI toggles wired to nothing (`moe` class), env vars documented but unread,
   perms rows for paths nothing creates (`/var/log/hal0`, `.first-run.lock` — §6.2).
4. **Filler/stale text**: lorem-ish strings, TODO/FIXME with dead owners ("ui-sweep-b"
   class — §1.3), docstrings citing retired docs (`PLAN.md §…` class — §5/§9), comments
   asserting falsehoods ("runs as root" class — §6.2), stale example/slot/model names in
   help texts and errors, hardcoded versions (§3 VERS-flash), `console.log`/`print`
   leftovers.
5. **Doc-vs-code drift**: cli.mdx flags (§5.5), connect-mcp/realtime auth sections (§4.5),
   CONTRACTS.md/endpoints.ts stale claims (§2) — one sweep each, with the parity tests
   tightened so the class can't recur.

DoD for the sweep: inventories exhausted (grep classes return only adjudicated keeps),
scar baseline strictly lower than drive start, zero placeholder surfaces reachable in the
shipped UI without an owner row.

## 5. Phases (1 → 4) with DoD

### Phase 1 — Sync wiring (start immediately; ~4 parallel teams)
From assessment §-refs; Phase-0 items are done — do not redo.
- **MCP-sync** (API class): tier-a catalog adds (rename gated · by-id/by-name/resolved/
  state reads · PATCH defaults · model default/duplicate · pulls list/delete · system-info
  · bench queue delete) + `memory_recall` on admin + read/write reclassification +
  EXCLUDED set. (Route-sync pin landed in Phase 0.) §4.3.
- **CLI-auth+verbs** (CLI class): `hal0 auth status|rotate|require` · `slot rename` ·
  `model default|update [--check]|pull --cancel` · `hal0 ports` · `hal0 board …` ·
  `hal0 chat --brain` · `import-backup` SQLite chain. §5.2.
- **UI wiring** (UI class): DuplicateModelDialog → real route · mock realignment
  (capabilities envelope, status/update shapes, dead auth rows) · endpoints.ts/CONTRACTS
  doc-sweep · ui-sweep-b ENDPOINTS consolidation. §1.2/§2.
- **Missing routes** (API class, serialize behind MCP-sync merge): `GET /api/stats/requests`
  (rollup shape frozen client-side) · `GET /api/doctor` · `GET /api/auth/exposure`;
  `flag-report` goes into FLAGS-own DoD, not here. §1.2.
- **VERS-flash + NAMES-stale** (UI class) — §3 above.
- DoD: every "API-lane request" comment in ui/src either has a live route or a board row;
  CLI reaches every merged R3/R4 ability; admin MCP exposes tier-a; both defect lanes
  closed with regression e2e.

### Phase 2 — Structural + the seamless drawers (~4 teams; the release core)
- **P3-routers inc 3** (API): pull-orchestration extraction (Opus) → typed bodies
  (audit first; new routes born Pydantic; open `typed-bodies-rest` row) → **route-map
  autogen** (Opus; settle §4.4's 3-gap addendum first). DoD: models.py ≤550 / slots.py
  ≤800, `request.json()` = 0 in owned files, autogen live with security overlay.
- **FLAGS-own** (MODEL+UI, the drive centerpiece): backend migration per
  `spec-flags-ownership.md` (+ `GET /api/migrations/flag-report` in DoD) · **DRAWER-shape
  UX push** (§3) · CLI tranche (deprecate `--provider/--hardware/--backend`, delete the
  client-side hardware probe) · golden #5 (no profile read at launch). Rides the P2-config
  window for migration.
- **lifespan() split** (API, Opus): phase-split `api/__init__.py:862` (~540 lines, 44
  `app.state` touches) + typed app.state + BootReport. Do NOT touch `create_app()` (§11 —
  its centrality is fixture noise). Importer flips + `_probe_power` extraction + sunset
  stamps ride this lane. §3.
- **Settings data-seam completion** (UI): remaining pages onto typed settingsClient ·
  wire-or-demote the two placeholder pages · ESM continuation (47 window-global modules,
  ratchet rule) · CSS-era consolidation. §1.3.
- DoD: routers parse→call→render only; slot/model drawers ship the new shapes seamlessly;
  boot is phased+typed; settings pages all on the seam.

### Phase 3 — Memory & Hermes finish (~2 teams + deploy window)
- **MCP-mem-hindsight** (HERMES): rename to `hindsight_recall/retain` + implement
  `reflect` + `local_external` config layout + both parity copies + provisioner prompt.
  §4.5.
- **Brain-lane relocation** (HERMES): 5 RELOCATE steps → hal0-api lifespan (pairs
  naturally with the lifespan split — sequence after it merges). §7.
- **Drift-watch fixture adds** + **hermes-bump runbook**. §7.
- Deploy window: re-run Phase-5 plugin liveness + Phase-6 HP-executor first contact
  (both-boxes). §7.

### Phase 4 — Migration windows, launch, RELEASE (orchestrator-run live steps)
- P2-memory (fixtures-rehearsed, never mutate lxc105) · P2-config (+ FLAGS-own migration
  same window) · P2-updater-b (**verify + trim, not build** — pipeline is implemented,
  1,918 lines) · P3-runtime-db (M5 coordination) · SLOT-B live flip (atomic) ·
  ComfyUI repin + cpu lineage · golden-paths deploy runbook on halo143 · R5 cutover
  (side-by-side, lxc105 rollback).
- **Release prep (new, the point of the drive):** version bump proposal (the tree says
  `0.5.0-alpha.1`; propose the real next version), CHANGELOG rewrite for the release
  (rework story told once, scars→results), README/dashboard screenshots refresh, upgrade
  notes (Honcho migration, slot-id flip), tag plan. Present as a reviewable package —
  the user decides the version and pulls the trigger.

## 6. Decisions owed to the user (surface at phase boundaries; none block Phase 1)
Root `AGENTS.md` re-delete vs pointer stub · ComfyUI host-net loopback veto · updater
nightly channel drop-vs-add · cpu-runner lineage · HP-voice/automation/context
promote-or-defer · god-module LOC tracking yes/no. Ask **once per boundary, batched**,
with a recommendation each.

## 7. Conventions (unchanged, binding)
Board single-writer (you, this drive) · one owner per fact · no ghost citations, no ADR
tree (decisions inline in ARCHITECTURE.md) · ✔ needs merge SHA + verify evidence ·
deploy-affecting = both boxes (150 privileged / 143 unprivileged), recorded per box ·
capped gate on every code touch, dangling-link grep on docs · land on descar · use
`graphify query` before grepping but filter its metrics (assessment §11: test-edge /
cross-language / same-file noise; SlotManager is the real waist, `create_app` is not);
re-run the semantic pass (not just `update`) if doc→code edges are needed.

## 8. Why one session, not two (recorded)
Two sessions were considered (backend/structural vs UI+sweep). Rejected: the board is
single-writer; FLAGS-own couples the backend migration to the drawer UX in one seam; and
merge waves interleave across classes. Parallelism comes from worktree teams under one
orchestrator. If a second session is ever added, the clean cut is the SWEEP workstream
(§4) on a docs/UI-only branch handing row deltas back — but default remains one.

---

_Supersedes `handoff-r5-endgame.md` as the paste-in prompt (its evidence base —
`r5-sync-assessment-2026-07-19.md` — remains authoritative; where notes disagree, the
assessment's `path:line` + a fresh read of the tree win). Prepared 2026-07-19, after
Phase 0 landed @ `278b32a8`._
