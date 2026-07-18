# hal0 rework — handoff: single primary orchestrator (R3-B / R4 / R5 + finish line)

You are the **sole primary orchestrator** for the remaining hal0 rework — all planned development,
end to end. The prior session ran as one of TWO concurrent orchestrators on this clone; that is now
consolidated to one. **Run as a single orchestrator.** Do not spawn a second driver on this clone.

Do NOT re-derive state from this doc — the authoritative content lives in the referenced files. Read
those; this is a pointer map + the deltas from the last session.

## 0. First actions (in order)
1. Read `/home/mint/REWORK_BOARD_PROTOCOL.md` (how to operate: reading order, single-writer rule, lane
   lifecycle, verify/merge/checkpoint discipline, agent-dispatch skeleton, hard rules). In-repo copy:
   `docs/rework/board-protocol.md`.
2. Read `/home/mint/REWORK.md` (spec: finish line, R1–R5, invariants, golden-paths, per-lane DoD) and
   `/home/mint/REWORK_BOARD.md` (live status board — the ONLY status source; every lane row is current).
3. Run `bash /home/mint/hal0-status.sh` (live snapshot) and `/home/mint/hal0-board.sh` (tmux monitor).
4. Confirm base: `git -C /home/mint/hal0 fetch origin && git log --oneline -1 origin/rework/descar`.

## 1. State at handoff (2026-07-18, ~16:20)
- **`rework/descar` = `b02447a4`** ("merge(hermes): extract runtime role-slot policy"); CI + Playwright(γ)
  in-flight on it at handoff — confirm green before treating it as a base. **`main` = `6aa565b8`**
  (tag `rework-R2.1`). descar is AHEAD of main by all of R3-A + Hermes HP-core/HP-role — collapse to a
  tagged R3 (and/or R4-stage) checkpoint once green (see §4).
- Scar 202 / baseline 202 (`scripts/scar_baseline.txt`). Keep `scripts/check_sunset.py` green every merge.
- **Open PR #1309** ("rework/descar: R3/R4 Hermes integration staging") is the CI gate for descar pushes —
  keep it OPEN (a closed PR ⇒ pushes get no CI). PR #1305 (registry-prune) is a separate open PR.
- Worktrees present: main checkout (`b02447a4`), `.claude/worktrees/hermes-role-policy` (`a0173453`,
  the source of the merged role-policy work — can be reaped if fully merged), `.claude/worktrees/p3-brain`
  (`b66b5e1e`, banked, UNMERGED — see §3).
- Deploy target unchanged: **halo143** (10.0.1.143), side-by-side. **Never push/deploy to lxc105**
  (`hal0`/`hal0lxc`) — untouched live reference.

## 2. Landed this session — R3-A (do NOT re-audit; board rows carry detail)
Three disjoint-class lanes built by isolated worktree agents, independently verified, merged, CI-green,
worktrees reaped. See `REWORK_BOARD.md` rows for commits/verify:
- **SLOT re-key increment A** (`e0fd6d7c`) — slot-id identity + PortAuthority wired live; `/rename`,
  `/by-id`, `/by-name`, `/api/ports` 5th source; drop `port=8081`; non-destructive `fold_identity()`
  boot-fold. Additive/bijective (name↔id) — internal `dict[int]` re-key + destructive M5 rename DEFERRED
  to increment B (§4).
- **KB-1-tail** (`5f5eb913`) — WS `?api_key` log scrub on `uvicorn.error`, origin gate before tier-auth,
  sliding-window login 429. (Security UI *page* was pulled to a later UI wave — see follow-ups.)
- **P3-ui-dataseam** (`0c93a1f3`) — typed `settingsClient` façade + single `reloadClass` source
  (ApplyBadge amber-chip fix) + `useSettingsForm`; Backend/GPU + Model-Defaults pages.
- **Hermes lanes** (prior primary): HP-core merged (`b1115b9d`), HP-role-api / runtime role-slot policy
  merged (`b02447a4`).

## 3. Banked, unmerged — decide disposition
- **`rework/p3-brain` @ `b66b5e1e`** — first-class `src/hal0/brain/` (zero-Hermes-dep), `/api/brain/chat`
  primary + `/api/board/chat` thin alias (board_chat.py rebinds via `sys.modules`), +1 exposure.py ADMIN
  rule, 67 tests green. Built + independently verified last session but NOT merged (was handed across the
  two-orchestrator split). **This is the P3-brain lane (R4 §G/§16.1).** Rebase on current descar, re-verify,
  merge — OR rebuild if the Hermes work changed the surface. Invariant to preserve: core works without Hermes.
- **`rework/hermes-role-api` @ `a9fe36bd`** — earlier HP-role-api scaffolding; likely superseded by the
  merged `b02447a4` role-policy. Confirm, then delete the branch if redundant.

## 4. Remaining planned development (the whole roadmap — reference REWORK.md + board, don't restate)
Drive these as waves; one file-mutating lane per collision class at a time, disjoint classes in parallel.
Collision map is unchanged: SLOT-hot files (`slots/manager.py`, `api/routes/slots.py`,
`providers/container.py`) serialize the SLOT class.

**R3-B (SLOT-hot serialized on increment A):**
- **SLOT re-key increment B** — internal `dict[int]` re-key of the 7 in-memory dicts + destructive M5
  unit/file/podman rename (`hal0-slot@<id>`, `<id>.toml`, `<id>/state.json`). Needs a WIDENED fence: the
  11 name-keyed test files (`test_fail_watcher`, `test_pressure_eviction`, `test_pulling_serving_idle`,
  `test_adopted_slot_eviction`, `test_manager_readiness_api`, …) + 7 cross-fence files (`container.py`,
  backends/logs/installer/comfyui/journal, `board_chat.py`, `_settings_apply.py`) + real-hardware smoke.
  Spec `hal0-specs/spec-p3-slot-identity-ports.md` PR3–6.
- **P3-quadlet** (shares `container.py`) — spec `hal0-specs/spec-p3-quadlet.md`.
- **SlotManager-deepen** — `inspect/apply(desired)/delete/subscribe` (REWORK.md §E, review #4). Use the
  `codebase-design` skill.
- **P3-routers** — thin `models.py`/`slots.py` → service modules; spec `hal0-specs/spec-p3-routers.final.md`.
  Plus the cheap **route-collision test** (reject literal shadowed by param routes).

**R4 (Brain + Hermes adapters):** P3-brain merge (§3); HP-memory, HP-provider, HP-voice, HP-executor
(gated on KB-4/5/6), HP-automation; KB-2/3 (brain read-only + approval-gate), KB-4/5/6 (SQLite board +
dispatch seam + ETag). Compat prerequisite before adapters: expand `hermes-sdk-diff` to the full contract
surface. Design: `docs/superpowers/specs/2026-07-18-hal0-hermes-integration-suite-design.md` +
`docs/rework/hermes-official-integration-research.md`.

**R5 (Surface + launch):** §21.4 doctor (`hal0-specs/spec-21-4-doctor.md`), P4-docs collapse, P4-tests
(CI tiers + flake stabilization), P4-rules, §21.11 golden-paths (pull earlier — review #7), Security UI
page (deferred from KB-1-tail).

**Migration-window lanes (orchestrator-run LIVE steps, NOT agents):** P2-config truth-collapse,
P2-memory Honcho→Hindsight (`hal0 memory migrate`, `src/hal0/cli/memory_migrate_commands.py`),
P2-updater-b, P3-runtime-db (state.json/pull-jobs/events → SQLite). Procedure in REWORK.md §Migration.

**Deploy-assisted (need halo143 SSH; can't run in CI):** redeploy halo143 from descar (clears hot-patched
§7.4 inc5), then `hal0 doctor all`; §20 bench sweep (`hal0-specs/spec-bench.final.md`, needs GPU box);
store-GC deploy-only golden-paths.

**Checkpoint collapses:** descar → `main` at each green, deployable checkpoint; tag `rework-R<n>`.
descar is currently ahead of main by R3-A + Hermes — the next collapse is due once R3/R4-stage is green.

## 5. Follow-ups logged (fold into waves)
- **C7d flake** — `ui/tests/e2e/specs/slot-drawer-profile-v3.spec.ts:202` ("C7d — NPU slot: profile
  rendered as fixed text") fails only in the full CI γ suite under load (position ~337/433); passes in
  isolation, passes local full γ (422/0), passed CI on re-run. Stabilize in P4-tests. Not a code defect.
- **SLOT increment B** widened-fence requirement (§4).
- **Security UI page** (deferred from KB-1-tail to a UI wave).

## 6. Lessons carried forward (avoid re-learning)
- **Single orchestrator, single clone.** Two concurrent orchestrators this session caused: origin/descar
  advancing under an in-flight verify, a transiently "dirty" shared checkout (mistaken for lost work),
  and ~3 concurrent-edit races on `REWORK_BOARD.md`. All recovered, nothing lost — but the single-writer
  board rule and single-driver rule are non-negotiable. If you must parallelize, use ISOLATED worktree
  build agents only; the orchestrator alone merges/pushes and owns the board.
- **Capped verify MUST include `ruff format --check`** (not just `ruff check`) — CI format gate.
  Note: bare `ruff check .` shows ~21 PRE-EXISTING repo errors (installer/bench/packaging/scripts) — scope
  ruff to the lane's changed files to judge a lane; CI tolerates the pre-existing set.
- **Closed PR ⇒ no CI on push.** Keep an open PR (currently #1309) or `gh pr create`.
- **Full-suite-only failures** the capped local run misses (`tests/api` HANGS locally on podman — never
  run the whole tree; name specific files). Expect + fix-forward from CI.
- **Rebase, don't trust `git diff origin..HEAD`** — worktree bases advance; diff `merge-base..HEAD` and
  trust the trial-merge. Rebase retained lanes onto the CURRENT descar before merge; two lanes sharing a
  file (e.g. `api/__init__.py`) auto-merge cleanly when regions differ.
- **Escalate policy/security-guard conflicts** to the user; do not fix-forward by gutting guards.

## 7. Hard constraints (non-negotiable — full list in the protocol doc)
- Never push/deploy to lxc105 (`hal0`/`hal0lxc`). Deploy target = halo143.
- Scar ratchet monotonic-down; `check_sunset` green every merge.
- New route ⇒ `security/exposure.py` deny-by-default classification or CI fails.
- Next DB migration number = **005** (001 registry · 002 metrics · 003 store · 004 slots/ports).
- Liveness/preflight probes hit OPEN endpoints (`/api/health`), never admin-gated (`/api/status`).
- venv `/home/mint/hal0/.venv/bin/python`; worktree agents run `PYTHONPATH=$PWD/src`. Build scratch under
  `~/.cache`, not `/tmp`. Frontend verify from `ui/`: `npm run lint` + `typecheck` + `build`; γ =
  Playwright (`npx playwright test`).

## 8. Standing user mandate
"Drive everything." Spawn isolated Opus worktree agent teams for parallel lanes; the orchestrator reviews +
merges + handles all git/CI; collapse descar → main at each green checkpoint (tag `rework-R<n>`); only
interrupt for genuine decision blockers (policy/security-guard conflicts). Trunk-collapse milestones land
on the user's "go".

## 9. Suggested skills
- **`codebase-design`** — SlotManager deep-module interface (`inspect/apply/delete/subscribe`), seam calls.
- **`tdd`** — SLOT increment B + Hermes adapters (correctness-critical; red-green-refactor).
- **`code-review`** — review each lane branch pre-merge (Standards + Spec axes).
- **`verify`** — exercise SLOT rename/port-cleanup + brain-without-Hermes end-to-end, not just tests.
- **`diagnosing-bugs`** — CI-caught full-suite failures (e.g. the C7d-class flakes).
- **`resolving-merge-conflicts`** — R3-B lanes stack on SLOT-hot files.
- **`security-review`** / `/security-review` — Security UI page + any new routes.
- **`run`** — drive the app / screenshot P3-ui settings pages; redeploy validation.
- `caveman` mode is active for terse comms; `minimax-swarm` optional for cheap bulk (P4-docs).

## 10. Reference artifacts (don't duplicate — read at source)
- Spec: `/home/mint/REWORK.md` · Board: `/home/mint/REWORK_BOARD.md` · Protocol:
  `/home/mint/REWORK_BOARD_PROTOCOL.md` (+ `docs/rework/board-protocol.md`).
- History/derivations (superseded, background only): `/home/mint/hal0-rework-plan.md`.
- Lane specs: `/home/mint/hal0-specs/` (slot-identity-ports, quadlet, routers.final, bench.final,
  settings, 21-4-doctor).
- Hermes design: `docs/superpowers/specs/2026-07-18-hal0-hermes-integration-suite-design.md`,
  `docs/rework/hermes-official-integration-research.md`.
- Prior handoffs: `/tmp/hal0-rework-handoff-r3-parallel.md`, `hal0-rework-handoff-session3.md`,
  `/tmp/hal0-rework-handoff-session4-continue.md`.
- Monitor: `/home/mint/hal0-board.sh`, `/home/mint/hal0-status.sh`.
