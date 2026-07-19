# Handoff — R5 Phase 4: deploy window → release cut (paste-in prompt)

> **You are picking up the hal0 R5 rework at Phase 4.** Phase 3 (memory/Hermes finish + SWEEP,
> waves 1–3, FLAGS-own centerpiece) is merged and the board's checkpoint table calls it done.
> Your mission is Phase 4 as scoped in `handoff-r5-drive2.md` §5: **the live migration windows,
> the halo143 deploy rehearsal, cutover, and the release-prep package** — leading into a
> Phase-5 collapse of `rework/descar` → `main` at `v1.0.0`.
>
> Read in order: (1) this handoff · (2) `docs/rework/REWORK_BOARD.md` (live status — R5 section
> + the four Phase-3 wave sub-tables + "Migration-window lanes" table) · (3)
> `docs/rework/deploy-validation/2026-07-19-r5-install-validation.md` (today's live findings,
> stamp r5v1) · (4) `docs/rework/handoff-r5-drive2.md` (operating model this drive follows —
> team topology, gitops, model tiering; superseded only where this doc says so) · (5)
> `docs/rework/REWORK_BOARD_PROTOCOL.md` (board mechanics) · (6) `docs/rework/REWORK.md`
> (finish line + DoD) · (7) `docs/rework/r5-sync-assessment-2026-07-19.md` for any `§-ref` cited
> below — **re-verify `path:line` cites before acting, code has moved since 2026-07-19's
> assessment was written.**

---

## 1. Tree state (verify before anything — tips move)

- **Integration branch:** `rework/descar`. **Tip at handoff time: `8d15b04f`**
  (`docs/rework/deploy-validation/2026-07-19-r5-install-validation.md` install-validation
  record). Confirmed identical on `origin/rework/descar` (fetched clean, no divergence) as of
  2026-07-19. Re-run `git fetch origin && git rev-parse rework/descar origin/rework/descar`
  before you branch — do not trust this hash once time has passed.
- **Phase 3 is fully merged**, three waves, all consolidated-gate green:
  - **Wave 1** (`dc0f5cf0`→`577ee462`, board row `8ffc990a`): `MCP-mem-hindsight`
    (`hal0_memory_*` → `hindsight_{recall,retain,reflect}`, `4c2e5f14`) · `brain-lane-relocate`
    (5 `RELOCATE(brain-lane)` steps out of `install_hermes` into api lifespan, `577ee462`) ·
    `drift-watch+hermes-bump` (fixtures + 9-step bump runbook, `23c92424`) · `SWEEP-delete`
    (4 dead symbols, `7e062603`).
  - **Wave 2** (`8ffc990a`→`5bf5a743`, board row `b486d7e3`): `reconcile-wire`
    (`reconcile_listeners()` wired into `GET /api/ports`, `cdb9ef83`) · `sunset-ratchet` (first
    real `HAL0-SUNSET` stamps, 12 items, `48a9b48a`) · `persona-flag` (`--reset-personas` on
    `hal0 agent install hermes`, `5bf5a743`).
  - **Wave 3, the centerpiece** (`b486d7e3`→`0a0bc6e8`, board row `8903a430`): **FLAGS-own §2**
    (flags own by MODELS — argv chain strips `profile`/`slot_overrides`/`extra_args`, model
    `.defaults` carries materialized flag text, `d4253f8f`) + its migrator
    (`config/migrations/slot_flags_fold.py`, code-complete, **not run** — deploy-window-gated,
    same commit) + `sunset-retarget` (all stamps re-pointed `v0.10.0`→`v1.0.0`, `0a0bc6e8`).
  - After wave 3: one fix-forward test commit (`471c365a`, see concurrent-writer note below)
    and one docs commit recording the live install-validation (`8d15b04f`, this session).
- **⚠ CONCURRENT-WRITER CAVEAT — a second Claude session has pushed to `rework/descar`.**
  Direct evidence from the local reflog:
  ```
  8d15b04f rework/descar@{0}: commit: docs(deploy): R5 install-validation record
  471c365a rework/descar@{1}: reset: moving to origin/rework/descar
  71baca26 rework/descar@{2}: commit: test(updater): align unit-rerender freshness check with FLAGS-own
  8903a430 rework/descar@{3}: commit: docs(board): Phase 3 wave-3 merged …
  ```
  This session had locally drafted `71baca26` (`test(updater): align unit-rerender freshness
  check with FLAGS-own`) to fix the same gap; before it could push, `origin/rework/descar` had
  already advanced past that point with an **equivalent commit authored by `Claude`** (not
  `Alexander`): `471c365a fix(tests): migrate unit-rerender test to FLAGS-own semantics`
  (`git log --format='%an'` shows author `Claude`, timestamp `2026-07-19 19:25:15 +0000`, vs.
  every other descar commit today authored by `Alexander`). This session reset onto the remote
  tip rather than force-pushing a duplicate fix. **Conclusion: someone else's agent session is
  actively landing commits on `rework/descar` outside this handoff's authorship.** Before you
  dispatch any Phase 4 lane: `git fetch origin && git log --oneline HEAD..origin/rework/descar`
  and reconcile/rebase first — do not assume you are the sole writer the way `handoff-r5-drive2.md`
  §8 stipulated for drive 2. Coordinate (or at minimum diff-check) before merging anything that
  touches files the other session might also be touching (FLAGS-own/`argv.py`/tests were its
  last known area).
- **CI + γ gating protocol (unchanged from drive-2, `REWORK_BOARD_PROTOCOL.md` lifecycle
  §3–4):** every lane gets an independently re-run **capped gate** before merge (`ruff check` +
  `format --check` + import smoke + `scripts/check_sunset.py` + targeted pytest <90s for
  Python; `tsc` + `eslint` + `build` + targeted γ for UI) — never trust a lane's self-report.
  Trial-merge against current `origin/rework/descar` before merging for real
  (`git merge --no-commit --no-ff origin/rework/descar; git merge --abort`). Full CI (GitHub
  Actions pytest) is the real gate and only runs on an **open** PR — a closed PR gets no CI.
  Push waves batch 2–4 lane merges; consolidated full-suite gate before each wave push. CI red
  on descar = **stop-the-line**, fix forward before any new merge, never rewrite descar history.

## 2. What Phase 4 is

Per `handoff-r5-drive2.md` §5 Phase 4 (unchanged scope, now the active phase): **the live
migration windows, launch, and RELEASE** — orchestrator-run live steps against real boxes, not
worktree agent lanes. This is the deploy/rehearsal/cutover window leading into the Phase-5
release-cut package. Concretely:

**Migration windows** (board "Migration-window lanes" table, `REWORK_BOARD.md` §"Migration-window
lanes" — all currently `☐` todo, none started):
- **P2-config** — `capabilities.toml` → derived view, one apply engine. Runs the **FLAGS-own
  migrator** (`slot_flags_fold.py`) in the same window per the FLAGS-own board row
  (`deploy_state: migrator run = deploy-window`) — 3-release window + create-on-select.
- **P2-memory** — Honcho → Hindsight migration per workspace, then ordered deletion. Use the
  existing `hal0 memory migrate --from honcho --to hindsight`; seed deterministic Honcho
  fixtures on a fresh halo143 (or a sanitized read-only lxc105 export); verify persisted
  `[honcho]` tolerance. **Never mutate lxc105** — it is the untouched live reference box, not a
  deploy target (per board note under "Folded from lxc105 live session").
- **P2-updater-b** — one cosign+swap+rollback path. Board note: **verify + trim, not build** —
  the pipeline is already implemented (1,918 lines); this window is a live rehearsal, not new
  construction.
- **P3-runtime-db** — `state.json`/pull-jobs/events → SQLite, one table at a time. Coordinates
  with the SLOT-B increment-B M5 migrator (see §3 below) — the board flags a **state.json
  double-touch risk**: M5's rename touches `state.json`, which this lane later moves to SQLite;
  either fold slot-state into the SLOT wave or scope M5 so runtime-db never re-migrates the same
  file (decide at dispatch, per "Fable plan-review adds" §Migration-number allocation note).

**Deploy rehearsal + cutover** (drive-2 §5 Phase-4 bullet list):
- **SLOT-B live flip** (atomic) — the SLOT increment-B M5 one-shot name→id migrator
  (`migrate_id_keying.py`, code-complete/inert) goes live: `hal0-slot@<name>` → `@<id>` unit
  rename + podman rename + M5 on real state + runtime path/unit flip to id
  (`_state_file`/`_config_file` + unit rendering) — must land atomically with M5 going live.
  Board deploy_state for that row: **"held for deploy (halo143 migration window)."**
- **ComfyUI repin + cpu lineage** — not detailed further on the board; treat as its own small
  live step, cross-check against the `hostnet-render` row's open item (ComfyUI web UI is now
  **loopback-only under host net**, was LAN `:8188` — a user-veto window is still open on that
  change).
- **golden-paths deploy runbook on halo143** — `docs/rework/golden-paths-halo143-runbook.md`
  scripts the deploy-only halves of the 15 `§21.11` golden-path scenarios (both-boxes policy);
  the CI-runnable subset (#9/#10/#14/#15) already landed as integration tests
  (`golden-paths-early` row, `✔`).
- **R5 cutover** — side-by-side, lxc105 stays rollback reference (never deployed to directly).
- **Live-validate the Phase-0 deploy-affecting three, both boxes:** `hal0.target`
  reboot-autostart, model-store `PermRow` perms, uninstaller sync (`INSTALL-target` row,
  `deploy_state: LIVE VALIDATION DEFERRED — both boxes (150/143), Phase 4`). **Partially
  addressed today**: 150's `hal0.target`/services confirmed active+enabled
  (reboot-autostart-ready) in the 2026-07-19 validation run (see §4); 143 never reached this
  phase (blocked at preflight).

**Release prep** (drive-2 §5, "the point of the drive"):
- Version-bump proposal — release version is **pinned at v1.0.0** (user decision, see §5); this
  step is now "execute the pyproject/UI bump," not "propose a number."
- CHANGELOG rewrite for the release (rework story told once, scars→results).
- README/dashboard screenshot refresh.
- Upgrade notes: Honcho→Hindsight migration, slot-id flip (both are live-migration-shaped —
  document the operator-facing steps, not just the code).
- Tag plan for `v1.0.0`.
- Present as a reviewable package — **the user decides the version and pulls the trigger**
  (version itself is already pinned; the "pull the trigger" step is the actual cutover/tag/push).

## 3. Deferred / carried-in work from Phase 3 (still open, now in Phase-4's lap)

- **FLAGS-own §7 — slot field-removal (deferred).** Board row `FLAGS-own §7 tail (deferred)`,
  status `☐`. §2 (flag **text** ownership) shipped in wave 3; §7 (full slot-purity — remove
  slot `device`/`chat_template`/`mtp` **typed fields** and sever those launch axes) is a
  deliberate follow-lane. **Those three axes remain functional and sunset-stamped, not
  removed** — they still work exactly as before, just marked for `v1.0.0` removal. The migrator
  already handles the data fold if/when §7 lands; this is a separate build lane, not a Phase-4
  live step.
- **FLAGS-own migrator — code built, gated on the deploy window.** `config/migrations/slot_flags_fold.py`
  is complete (190 lane tests), does the one-shot fold of slot effective-tune into model text
  with provenance tracking, managed-flag split (`-ngl`/`-c` → typed fields, never
  `extra_args`), and a **divergent-share refusal path**: if a multi-slot→one-model fold would
  require diverging tunes, `apply` raises and refuses rather than partial-writing. Idempotent,
  dry-run by default; `apply` requires `deploy_window=True`. **This is the P2-config window's
  payload** (§2 above) — it has never been run against real state.
- **typed-bodies-rest — audit banked, ambiguity flagged.** The board's R5 summary line says
  "typed-bodies (audit banked)" but there are **two distinct open items** under that umbrella
  and I could not find a single row that unambiguously matches "15 sites, 400-vs-422 preserve"
  as one thing — flagging rather than guessing:
  1. **P3-routers inc 3 remainder** (`REWORK_BOARD.md` row `P3-routers (inc 3)`, `☐`): the
     pull/update-pull orchestration extraction plus **15** `request.json()` sites in
     models/slots routers needing typed bodies — explicitly gated on a "dashboard-key
     status-code audit first" (`r5-sync-assessment-2026-07-19.md:134-135`). The "400-vs-422
     preserve" precedent comes from inc-2's verify note: status codes were byte-preserved,
     keeping **422** over the spec's proposed 400 where they diverged (`REWORK_BOARD.md` row
     `P3-routers (inc 2)`) — the same discipline applies here.
  2. **`typed-bodies-rest`** (assessment §3, `r5-sync-assessment-2026-07-19.md:139-141`): **24**
     `request.json()` sites across **12 other route files**, explicitly called out as
     **owned by no lane** on the board today ("spec S10 defers to 'a future lane' that isn't on
     the board. Open a `typed-bodies-rest` row…"). This has not yet been opened as a board row.
  If the orchestrator dispatching Phase 4 meant the 15-site inc-3 item, dispatch it as
  `P3-routers (inc 3)`'s remainder; if the 24-site item, it needs a **new** board row first.
  Don't conflate the two counts when writing the row.
- **MCP route-map autogen — blocked on the §4.4 3-gap addendum.** `P3-routers inc 3 step 20`,
  its own lane. Spec (`spec-p3-routers.final.md:514-601`) is verified sound and walkable, but
  needs a one-page addendum settling three gaps first
  (`r5-sync-assessment-2026-07-19.md:215-226`):
  1. **Deny-by-default for unclassified auto-added routes** — under autogen every route lands
     in the map; unclassified must mean hidden from `tools/list` + a CI report, never
     fatal and never auto-exposed.
  2. **Transport exclusions** — PATCH support (§4.1) plus an exclusion predicate for
     SSE/stream/WS routes (logs/pull streams, events, board WS).
  3. **Re-key special-casing** — redaction/wrap overlays keyed on tool names must re-key on
     route id/response shape (the spec's "86-entry" figure is stale; actual count is 72).
  Sequence per the assessment: interim sync test → `build_admin_route_map(app)` + lifespan
  install + alias table → settle the addendum → keep `POLICY_NO_LOOSEN` + persona overlay.
- **halo143 podman-5.7 / unprivileged revalidation.** Not a code lane — a re-run of today's
  blocked install once the box-env keyring issue is cleared (§4). The `P3-quadlet` board row
  also flags a **podman-5 template refresh** as an R5 DEPLOY row (native `AutoRemove=`/
  `GroupAdd=`/`SecurityOpt=` keys + crash-path auto-remove) — currently 143 runs the 4.x
  compat-shim render (`PodmanArgs=`), same as 150; whether 143 gets the native-5.x template or
  stays on the compat path for this release is an open call.
- **Also still open from R3/R4 per the R5 checkpoint row** (`REWORK_BOARD.md` line 34–36),
  relevant to the deploy window: **quadlet `@`-name verify**, **M5 live rehearsal**, **runtime
  id-flip** (all folded into the SLOT-B live flip above) — and **HP-executor first contact**
  (both-boxes; `WORKER_BASE_PATH` is unpinned by contract fixtures, "validate against live
  Hermes at next both-boxes deploy" per the HP-executor board row) — and **host-net renderer**
  live validation (code done per `hostnet-render` row, ComfyUI loopback veto window still open).
  Both-boxes-runbook Phase 5 (Hermes plugin liveness) and Phase 6 (HP-executor first contact)
  were both marked **"not exercised"** in today's 150 run (§4) — still open.

## 4. Deploy-validation state (2026-07-19, stamp `r5v1`)

Full record: `docs/rework/deploy-validation/2026-07-19-r5-install-validation.md`. Ref under
test: `471c365a` (pre-dates the `8d15b04f` docs commit that recorded these findings).
**⚠ scope deviation**: neither box was fresh — both had hal0 `0.9.8` already installed and
serving; this was an **upgrade-in-place validation**, not a bare-metal fresh install (matches
what the R4-stage runbook actually assumes; the "fresh" framing in the original brief was
wrong). Non-destructive throughout, no data wiped.

| Box | Substrate | Result |
|-----|-----------|--------|
| **halo150** | Ubuntu 24.04, podman 4.9.3, py3.12, **privileged** LXC | **SUCCESS** — 141s, 13/13 steps, `INSTALL_EXIT=0`. **SHIP-READY on this substrate.** |
| **halo143** | Ubuntu 26.04, podman 5.7.0, py3.14, **unprivileged** LXC | **BLOCKED** at preflight step 1/13, `INSTALL_EXIT=1`. |

- **halo150 is GREEN and ship-ready**: O12 rootful seam (`podman_context:"rootful"`), uniform
  `PodmanArgs=` quadlet render, hermes-provision convergence markers, `/api/health` 200,
  autostart-enabled services, prior FAILED slot healed by the install. Uninstall gate
  deliberately **deferred** (chose not to destroy the live reference box).
- **halo143 was blocked by a box-environment condition, not a code defect (B1):** `podman run`
  fails with a `crun` keyring `EDQUOT` — root's kernel keyring byte-quota
  (`kernel.keys.maxbytes=20000`) is exhausted (`/proc/key-users` showed uid 0 at 19999/20000
  bytes), most likely from many repeated failed podman healthcheck units on that box. The
  installer's refusal to proceed is **correct behavior** — a fresh slot genuinely could not
  start either. **Remedy: reboot the CT to clear the leaked keyring** (or
  `keyctl clear @s`/kill leaked holders, or raise `kernel.keys.maxbytes`), then re-run install —
  expected to pass identically to 150 since it's the same code. Per the open task list, a
  re-validation pass on 143 post-reboot is already queued as follow-up work.
- **Open code fix-forward lanes surfaced by the findings** (none are release blockers per the
  report's overall verdict — "No hal0 code blocker found"):
  - **M2** — installer's container-runtime preflight gate gives a *misleading* remedy on the
    EDQUOT case (tells you to check `nesting=1`/`keyctl=1`, which 143 already has correctly
    configured) instead of diagnosing keyring-quota exhaustion.
    (`installer/lib/preflight.sh` smoke probe, ~L474-492.)
  - **M3** — GPU gate false-passes on a gid/name collision: `preflight_gpu` only checks the
    `renderD128` gid maps to *some* named group, not specifically `render`. On 143,
    `/dev/dri/renderD128` is gid 993, which maps to group `clock` there (real `render` is gid
    991) — the gate reports PASS but hal0-user GPU access would actually be denied. (On 150 the
    same gid 993 correctly maps to `render`, so the gate is right there — this is a
    143-host-config issue the gate should still catch generically.)
  - **m1** — `doctor perms` reports Hermes ownership drift immediately after a fresh-ish
    install on 150 (should be clean; O3-class).
  - **m2** — slot quadlet renders `StartLimitIntervalSec` inside `[Service]` instead of
    `[Unit]` — systemd silently drops it, so the intended start-rate-limit never applies.
    Present on 150's 471c365a render, confirmed via generator log grep.
  - **m4** — `hal0 agent status hermes --json` emits empty stdout (non-JSON table form works
    fine).
  - Minor/cosmetic items (m3, m5, m6, c1–c3) are in the source doc; not release-blocking, worth
    a scan before the cutover write-up.
- **Access recipe** (for whoever runs the next validation pass):
  - `pve` (Proxmox host): `ssh -i ~/.ssh/thin-mint root@10.0.1.110`
  - `box150`: `pct exec 150 -- <cmd>` (from pve)
  - `box143`: `ssh halo143`

## 5. Decisions owed / pinned

- **Release version = `v1.0.0` — PINNED (user decision, recorded in the wave-3 board entry).**
  `pyproject.toml` (`0.9.8`) and `ui/package.json`/`ui/index.html` version strings are
  **deliberately not yet bumped** — that bump is scoped to the Phase-5 release-cut package
  (§2 above), done once at cutover, not incrementally. Sunset stamps already target `v1.0.0`
  (re-targeted from `v0.10.0` in `0a0bc6e8`).
- **`rework/descar` → `main` promotion**: user-go required, tracked as PR #1318. Watch its
  mergeable state (not just branch existence) — per drive-2 §2 gitops rule 6, a conflicting PR
  silently stops ALL Actions runs on descar until reconciled. Given the concurrent-writer
  situation in §1, re-check #1318's mergeable state before assuming it's still clean.
- **§4.4 MCP-autogen addendum** (deny-by-default / transport-exclusion / re-key special-casing,
  §3 above) needs ratification before the autogen lane can start — this is a technical-spec
  decision, likely orchestrator-level rather than user-level, but flagging it here since it
  gates a Phase-4-adjacent lane.
- **Carried forward from `handoff-r5-drive2.md` §6** (batch at the next decision boundary,
  each with a recommendation): root `AGENTS.md` re-delete vs. pointer stub · ComfyUI host-net
  loopback veto (now sharpened by the live `hostnet-render` finding — LAN `:8188` access is
  actually gone under host net, this needs a real user answer, not just a flag) · updater
  nightly-channel drop-vs-add · cpu-runner lineage · HP-voice/automation/context
  promote-or-defer (drive-2 recorded these as already demoted "post-core" 2026-07-18 — confirm
  that stands for the release) · god-module LOC tracking yes/no.

## 6. Gitops, model tiering, skills (unchanged from drive-2 — summarized, not restated)

**Gitops** (`handoff-r5-drive2.md` §2, `REWORK_BOARD_PROTOCOL.md` lifecycle):
- One lane = one worktree = one branch, off current `origin/rework/descar` (fetch-fast-forward
  first, per the concurrent-writer caveat in §1 — **do not branch off a stale local tip**).
- Small, typed commits (`feat|fix|docs|refactor(scope): …`).
- Merge to descar only after independent capped-gate re-run + trial-merge; consolidated full
  gate before each push wave; batch 2–4 lane merges per wave, one push per wave.
- CI red on descar = stop-the-line, fix forward, never rewrite history.
- Tear down each worktree + branch immediately after merge.
- Every merge gets a reviewer pass ("X-built, Y-reviewed + independently re-run" per board
  discipline) — drive-2 used Fable as sole orchestrator/reviewer; this session should apply the
  same discipline regardless of who's driving, especially given §1's concurrent-writer finding
  — **do not merge anything into descar without diffing it against what the other session may
  already have landed there.**
- Migration-window lanes (§2 above) are explicitly **orchestrator-run live steps, NOT worktree
  agents** — they touch live halo state and must never be dispatched as a build-agent lane.

**Model tiering** (drive-2 §2):
- **Haiku** — mechanical fan-out: grep inventories, dead-export sweeps, docs application from a
  precise brief, fixture realignment.
- **Sonnet** — standard lane builds, research/surveys, e2e authoring, board-delta drafting.
- **Opus** — the hard code tasks (though Phase 4's core work is live-step orchestration, not
  new backend builds — reserve Opus for any residual build lanes in §3, e.g. `typed-bodies-rest`
  or the MCP-autogen lane once its addendum is ratified).

**Suggested skills for this phase:**
- `graphify` — repo rule: `graphify query "<question>"` before grepping source
  (`graphify path`/`graphify explain` for relationships/concepts); read
  `graphify-out/GRAPH_REPORT.md` only for broad architecture review.
- `lean-ctx` — prefer `ctx_read`/`ctx_search`/`ctx_shell`/`ctx_tree` over native equivalents
  when the MCP tools are present in-session.
- `code-review` — for reviewing each lane's diff before merge.
- `resolving-merge-conflicts` — likely needed given §1's concurrent-writer situation.
- `verify` / `run` / Playwright MCP tools — for driving the live app during deploy validation
  rather than trusting tests alone (per drive-2 §3 DRAWER-shape acceptance bar: "reviewed
  against the running app, not just specs passing" — the same standard applies to deploy
  rehearsal).
- `caveman`/`cavecrew` — agents should emit caveman-compressed output for file:line tables and
  fragments to keep orchestrator context long across a phase this live-step-heavy.

---

_Prepared 2026-07-19, tip `8d15b04f` on `rework/descar`. Supersedes nothing — `handoff-r5-drive2.md`
remains the standing operating brief for team topology/gitops/tiering; this document scopes
Phase 4 specifically and should be read alongside it, not instead of it._
