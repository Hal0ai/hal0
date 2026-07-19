# Handoff — hal0 R5 drive-2 (paste-in brief for a fresh **Fable** orchestrator session)

> **This supersedes `handoff-r5-endgame.md` as the operating brief.** The endgame handoff
> remains the **phase-spec appendix** (its §4 Phase 0–4 breakdown is still the per-lane spec).
> This drive-2 layers the *operating model* on top — team topology, model tiering, total
> gitops, phase/todo tracking, the SWEEP workstream, and the release cut — and refreshes the
> state to "Phase 0 landed." Where the two differ on **state**, this doc wins; where they differ
> on a **lane spec**, the endgame handoff + the assessment's `path:line` evidence win.
>
> **You are Fable, the single orchestrator.** Read in order: (1) this handoff, (2)
> `handoff-r5-endgame.md` (phase specs §4), (3) `r5-sync-assessment-2026-07-19.md` (the
> `path:line` evidence — adversarially verified), (4) `sweep-inventory.md` (the overlooked-surface
> backlog produced 2026-07-19), (5) `REWORK_BOARD_PROTOCOL.md` (single-writer, lane lifecycle,
> verify/merge discipline), (6) `REWORK_BOARD.md` (live status), (7) `REWORK.md` (finish line +
> per-lane DoD). Specs per lane: `docs/rework/hal0-specs/`.

---

## 1. Where things stand (2026-07-19 — verify before editing, tips move)

- **Merged to `main`:** R1 (`ecdc0950`), R2/R2.1 (`6aa565b8`), R3 (`ab3e88f3`), R4 (`c91d0cf5`).
  The finish line's hard parts (auth, slot-ids/ports, SQLite substrate, convergent installer,
  Hermes integration) are landed.
- **Phase 0 (Wave 0 — correctness & security) LANDED on `rework/descar`** (`1c01dbab`, board
  `278b32a8`), pushed. Six lanes ✔: `SEC-mcp-clientid` (`5d7cd283`), `MCP-patch` (`b878ff9d`),
  `CLI-auth` (`d55aa36d`), `mock-prod-gate` (`54ccd580`), `INSTALL-target`+model-store-PermRow+
  uninstaller-sync (`1bb9bf70`), `SEC-hermes-mcp-cred` (`1c01dbab`). Live-validation of the
  deploy-affecting three (target, perms, uninstaller) is **deferred to Phase 4 both-boxes**.
- **⚠ descar is RED on γ** (Playwright): one real regression introduced by Phase 0 —
  `activity-log.spec.ts:152 "body is a BOUNDED scroll container"` (462 pass / 1 fail / 1 flaky).
  γ was green at `2ab6ae69`, red from `278b32a8`. Prime suspect: the `mock-prod-gate` GET-only
  gate / fixture-split starving ActivityLog's mock rows (a sibling fix `68181bc6` already patched
  the memory-specs for the same class). **A fix is in flight from the drive-1 session — confirm
  it landed and descar is green before opening Phase 1.** If not landed, this is your first lane.
- **Phase 0 residual tails (☐):** `api-logs-redact` (`/api/logs` streams raw journalctl,
  zero redaction — same redactor as SEC-mcp-clientid), `cli-auth-streamtest` (auth-on smoke
  tier per §5.1). Both small; fold into Phase 1's SEC/CLI collision windows.
- **`main` advanced past descar's base:** `462b28f` + #1315 (HF update-check) + #1316 (UI-API-1
  managed-arg gap). Any descar→main landing rebases onto **current** `origin/main`.
- **Concurrent writers exist.** The drive-1 session pushed follow-ups (`68181bc6`) after Phase 0.
  Before you branch anything: `git fetch origin && git log --oneline HEAD..origin/rework/descar`.
  Fast-forward local to origin first. **You now hold the board token** once drive-1 hands off.

## 2. The finish line (quoted, `REWORK.md`)

Complete when hal0 has: one authoritative model/config path · one Hindsight memory path · one
tool-calling loop · one slot-config apply engine · one settings apply engine · one model-store
resolver · one runner-image registry · SQLite-backed machine state + model metadata · stable
slot IDs + centrally managed ports · deny-by-default auth + exposure classification · a
convergent installer with a clear privilege seam · a small optional Hermes integration ·
**deployment validated on the new `halo` LXC.** Operating rule: *finish the simplification,
validate it on `halo`, merge it — don't add adjacent features.* This drive adds one clause:
**the surface must not lie, leak, stub, or ship filler** — see the SWEEP (§6).

---

## 3. Operating model — how you run this drive

### 3.1 Team topology (max parallelism, safe)
- **One Fable orchestrator (you).** Single board writer. You never hand-edit lanes' worktrees;
  you dispatch, gate, review, merge, and write the board.
- **Worktree agent teams, 3–6 live at once.** One lane = one agent = one git worktree = one
  branch. Parallel **across** board collision classes (SEC · MODEL · RUNNER · SLOT · INSTALL ·
  HERMES · API · UI · OBS · DOCS · DEPLOY); **serialized within** a class (two API-router lanes
  never edit `models.py` concurrently). This is the pattern Phase 0 proved.
- **Worktree isolation is mandatory** for any lane that mutates files, so parallel agents never
  collide in the tree. Read-only investigators (SWEEP discovery, audits) need no worktree.
- **A clean second-session cut** if ever wanted: the SWEEP's docs/UI-only slices on a separate
  branch. Not required — the single board serializes fine.

### 3.2 Model tiering (the user's standing rule — follow it)
| tier | use for |
|------|---------|
| **Haiku** | grep inventories, mechanical sweeps, string/filler edits, marker stamping, fixture vendoring, dead-row deletion |
| **Sonnet** | lane builds, research, most feature/refactor work, UI wiring, settings-seam, doc lanes |
| **Opus** | the four hardest code tasks only: **pull/update-pull extraction** (P3-routers inc 3), **`lifespan()` split** (api/__init__), **MCP route-map autogen**, **FLAGS-own migration** |
| **Fable** | every merge review, adversarial verification, board writes, and cross-lane reconciliation |

Discipline for anything non-trivial: **built (Opus/Sonnet) → Fable-reviewed → independently
re-run** before ✔. Agents should emit **caveman-compressed** output (`/caveman` style: file:line
tables, fragments, no filler) to keep your context long across the drive.

### 3.3 Total gitops (non-negotiable)
1. **One lane = one worktree = one branch.** Branch from current `origin/rework/descar`.
2. **Capped gate on every code/test touch** before merge: `ruff check` + `format --check` +
   import smoke + sunset guard + named pytest targets; UI: `tsc --noEmit` + eslint + build +
   targeted γ. Docs-only: the dangling-link grep. (Encapsulate in `scripts/lane_verify.sh` if
   not yet present — the plan sanctions it.)
2. **Consolidated gate before each push wave**; **stop-the-line on any red** — no green-on-top-of-red.
3. **Immediate worktree teardown** on merge (worktrees auto-clean if unchanged).
4. **Every merge records: board SHA + verify evidence + a Task mirror** (see §3.4). A row is ✔
   *only* with a merge SHA and verify evidence (protocol rule 4).
5. **Land on `rework/descar`** (one integration branch); merge descar→main at **phase boundaries**,
   rebased onto current `origin/main`. **Surface the descar→main checkpoint to the user** — don't
   auto-merge a phase to main.
6. **Deploy-affecting = both boxes** (150 privileged / 143 unprivileged), recorded per box, at Phase 4.

### 3.4 Phase + todo tracking across teams
- Mirror every lane as a **Task** (TaskCreate at dispatch → `in_progress` when the agent starts →
  `completed` only with merge SHA + green gate). Use `addBlockedBy` to encode the deps in §4.
- The **board is the source of truth for status**; Tasks are the live work-queue mirror so you can
  see what's in flight across all teams at a glance. Reconcile them at every merge.
- Keep `graphify-out/` current: `graphify update .` after code lands; re-run the **semantic pass**
  (not just `update`) after doc changes so doc→code edges stay live for the graph MCP.

---

## 4. Phases (specs in `handoff-r5-endgame.md §4`; this is the sequence + DoD)

Each phase is independently shippable; later phases assume earlier ones. **Phase 0 is done**
(§1). Open the SWEEP (§6) as a **standing workstream running in parallel with all phases** — its
mechanical slices soak the Haiku/Sonnet capacity that phase lanes don't.

### Phase 1 — Sync wiring (mostly S/M; lights up already-shipped surfaces)
Close the backend↔frontend↔CLI↔MCP contract gaps. Lanes (endgame §4 Phase 1 = spec):
`MCP-sync` (interim route-sync test now; then hand-add R3/R4 tools; `memory_recall`; EXCLUDED set),
`CLI-auth+verbs` (`hal0 auth status|rotate|require` + slot rename / model default / model update
[--check] / model pull --cancel / ports / board / chat --brain; fix `model import-backup`),
`UI wiring` (DuplicateModelDialog→real route; mock-layer realignment; endpoints.ts/CONTRACTS.md
doc-sweep; `UI-API-2` auth affordances; ui-sweep-b ENDPOINTS consolidation), the **four
declared-but-missing routes** (`GET /api/stats/requests`, `/api/doctor`, `/api/auth/exposure`;
`/api/migrations/flag-report` folds into the FLAGS-own DoD), plus the two Phase-0 tails
(`api-logs-redact`, `cli-auth-streamtest`).
**DoD:** no shipped surface references a route/verb/tool that doesn't resolve; MigrationBanner and
the auth surfaces are live or explicitly demoted; capped gate green; contract doc-sweep clean.

### Phase 2 — Structural decomposition (finish the simplification)
`P3-routers inc 3` (**Opus**: pull/update-pull orchestration out of `models.py` → typed request
bodies, new routes born Pydantic → **MCP route-map autogen**, its own lane, settle the §4.4 3-gap
addendum first). `typed-bodies-rest` (24 `request.json()` sites no lane owns). Importer flips +
`HAL0-SUNSET` stamps on the 4 one-release surfaces + retire `GET /api/slots/{name}` (lowers scar
baseline). `api/__init__.py` god-file: **Opus** target `lifespan()` (`:862`, ~540 lines, 44
`app.state.` touches) — phase-split + type `app.state` + BootReport; **do NOT** refactor
`create_app()` (`:1400`, centrality is fixture noise). `FLAGS-own` (**Opus**,
`spec-flags-ownership.md`): flags/device/chat_template → models; slots reduce to
id/name/model/port/state; profiles copy-on-stamp; add the flag-report endpoint to the DoD; CLI
tranche deprecates `--provider/--hardware/--backend`; rides the P2-config window. Settings
data-seam completion (`spec-settings.md`) + UI continuation (ESM conversion, CSS-era
consolidation, slot-modals/chrome round 2).
**DoD:** routers only parse→call→render; one authoritative model/config path; scar baseline down;
god-module LOC burned down per checkpoint.

### Phase 3 — Memory & Hermes finish
`MCP-mem-hindsight` (§4.5: rename `hal0_memory_*` → `hindsight_recall/retain`, **implement
`reflect`** — route exists, body missing per SWEEP; config → `~/.hermes/hindsight/config.json`
`local_external`; both parity-locked copies + parity test). Brain-lane relocation (5
`RELOCATE(brain-lane)` markers into the hal0-api lifespan; marker-count test is the tripwire).
Drift-watch blind spot (`hermes_cli/kanban_db.py` + kanban runs API + token-injection seam →
`pyproject tracked_files`; vendor a kanban_runs fixture after the Phase-4 live pass).
`hermes-bump-runbook` (full bump procedure).
**DoD:** one Hindsight memory path; `reflect` implemented; brain phases run in the api lifespan;
drift-watch covers the kanban seam.

### Phase 4 — Migration windows + launch (orchestrator-run LIVE steps, NOT agents)
Cut over on the new `halo` LXC; lxc105 stays as rollback, **never mutated**. `P2-memory`
(Honcho→Hindsight per-workspace migrate on fresh halo143, deterministic fixtures, then ordered
deletion), `P2-config` (capabilities.toml → derived view; sequence FLAGS-own migration here),
`P2-updater-b` (**re-scope: verify + scope-trim + delete**, pipeline already implemented in
`updater/updater.py`), `P3-runtime-db` (state.json → SQLite one table at a time; coordinate with
SLOT-B M5), `SLOT-B live flip` (`@name→@id` + podman rename + M5 on real state — atomic),
cross-repo (ComfyUI repin, cpu-runner lineage), and the **R5 cutover program** (redeploy halo143
from descar, `doctor all`, podman-5 quadlet refresh, side-by-side, cutover plan; re-run the two
deferred Hermes validations — Phase 5 plugin liveness, Phase 6 HP-executor first contact). Also
**live-validate the Phase-0 deploy-affecting three** (target autostart, model-store perms,
uninstaller) here, both boxes.
**DoD:** halo143 deployed from descar, green `doctor all`, both-boxes validation recorded, cutover
plan approved.

### Phase 5 — Release cut (new — this is what makes it "worthy of a brand-new release")
Only after Phases 1–4 DoDs and the SWEEP DoD (§6) are met, and descar→main is clean and green:
- **Version-bump proposal** to the user (current `0.5.0-alpha.1` in `ui/package.json`; propose the
  real release version — the version-flash placeholder must be gone, see SWEEP).
- **Release-story CHANGELOG** — the R1→R5 arc as a human narrative, not a commit dump.
- **Upgrade notes** — migration windows, config moves (`capabilities.toml`, hindsight config),
  deprecated-verb removals, both-boxes deploy steps.
- **Tag plan** — the `rework-R5` / release tag, who pushes (git-proxy blocks some tag pushes — the
  user pushed R4's via thinMint; confirm the mechanism).
- Package all four as a **go/no-go** for the user. Do not tag without explicit go.

---

## 5. SWEEP — standing workstream for the overlooked (the user's headline ask)

Runs in parallel with every phase. Backlog lives in **`sweep-inventory.md`** (produced 2026-07-19
by a 5-way read-only discovery fan-out: dead-python · dead-ui · stubs/NotImplemented ·
dead-settings/config · filler/placeholder strings). Pipeline:

1. **Discover (Haiku/Sonnet, read-only):** the inventory — file:line, evidence, confidence, disposition.
2. **Adjudicate (Fable):** for each candidate, confirm DELETE / IMPLEMENT / DEPRECATE(stamp
   `HAL0-SUNSET`) / KEEP-DOC / VERIFY. A false "dead" claim is worse than an omission — every
   deletion is grep-confirmed against src+tests first.
3. **Apply (Haiku for mechanical, Sonnet for anything with logic):** by collision class, in
   worktrees, capped-gate-verified, merged like any lane.

**Categories (see the inventory for the concrete rows):** dead code (Python + UI), stubs /
`NotImplementedError` / route-exists-impl-missing (notably Hermes `reflect`), dead settings /
config keys / env vars / deprecated flags, filler / placeholder / fake user-facing strings
(version-flash `0.5.0-alpha.1`, `change-me`, lorem, `example.com`, TODO-in-copy), forgotten
half-migrated surfaces, doc drift.

**DoD:** every prod-reaching filler string fixed; every confirmed-dead symbol/file deleted or
sunset-stamped; every real stub either implemented or removed; the **scar ratchet baseline drops**
to reflect the deletions and does not regress; the docs-reference ratchet stays green.

---

## 6. Decisions still owed to the user (surface; don't guess)

- **Root `AGENTS.md`:** the board says P4-docs deleted it (zero code dep); confirm it's gone or
  keep a thin pointer stub to `ARCHITECTURE.md#bundled-agents-v03` — never a content copy.
- **ComfyUI under host-net:** hostnet-render made the ComfyUI web UI loopback-only (was LAN :8188);
  user veto window still open.
- **Updater channel:** drop `nightly` from the API (CLI is stable-only) or add it to the CLI — they
  disagree today; resolve in P2-updater-b.
- **cpu-runner lineage:** wire `hal0-toolbox-cpu:v1` + manifest_key, or ratify vulkan-reuse with a note.
- **HP-voice / HP-automation / HP-context:** stay ⏸ post-core, or promote any into R5?
- **Release version number** (§4 Phase 5) and **god-module LOC burn-down tracking** per checkpoint.

## 7. Conventions — do not violate (from `REWORK_BOARD_PROTOCOL.md`)

1. **Board single-writer** — you hold the token; deltas from lanes, not direct edits by them.
2. **One owner per fact** (rule 1) — grep for an existing test/row/section before adding one.
3. **No ghost-doc citations** (rule 9) — every path/PR/file must exist; the docs-reference ratchet
   fails on a dangling link. **No ADR tree** — decisions live inline in `ARCHITECTURE.md`.
4. **Status legend** — ✔ only with a merge SHA + verify evidence.
5. **Deploy-affecting = both boxes**, recorded per box.
6. **Capped gate on every code/test touch**; docs-only = dangling-link grep. Ride board updates on
   merge pushes.
7. **Land on `rework/descar`**; it merges to main at phase boundaries, on user go.
8. **Use the graph** before grepping, but **filter its metrics** — raw degree/betweenness are
   inflated by test-edge/cross-language/same-file noise; SlotManager is the real coupling waist,
   `create_app` is not. Re-verify `path:line` before acting on a specific finding — tips move.

---

_Source of record: `r5-sync-assessment-2026-07-19.md` (evidence) + `sweep-inventory.md` (overlooked
surface). Phase specs: `handoff-r5-endgame.md §4`. This drive-2 is the operating layer. Prepared
2026-07-19 by the drive-1 orchestrator for the incoming Fable session._
