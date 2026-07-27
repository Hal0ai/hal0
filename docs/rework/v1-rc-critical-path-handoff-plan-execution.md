# hal0 v1.0.0 RC Critical Path — Handoff, Plan, and Execution Workflow

> **Date:** 2026-07-22
> **State:** Approved design; implementation in progress across isolated worktrees (status refreshed 2026-07-22)
> **Release endpoint:** Validated v1.0.0 release candidate; no tag or publication
> **Primary design:** `docs/superpowers/specs/2026-07-22-v1-rc-critical-path-design.md`

## 1. Mandate

Drive hal0 to a validated v1.0.0 release candidate by:

1. repairing and landing PR #1330;
2. finishing the R5 section 4-6 surfaces (Memory/Admin MCP, CLI, Installer/Uninstaller);
3. completing backend-to-UI contract parity, including security and migration UX;
4. completing the FLM 1.0 rework end to end; and
5. proving the result with CI plus live validation on halo150 and halo143.

The operator asked for AFK execution where safe, subagent use, and disciplined token use across tasks, models, and agents.

## 2. Original Handoff Snapshot and Current Execution State

The repository and PR facts in sections 2.1-2.3 are the immutable snapshot from the original handoff. Section 2.4 records the current execution state and supersedes only claims about whether worktrees, branches, or implementation work exist.

### 2.1 Repository

- Primary checkout: `/home/mint/hal0`
- Current branch: `feat/llama-set-rows` at `fceab946`
- Current checkout is dirty with user/agent artifacts. Do not clean, reset, stash, or overwrite them.
- Existing linked worktrees:
  - `.worktrees/memory-graph-slot-selector-main`
  - `.worktrees/seeded-profile-rework`
- `.worktrees/` is ignored by `.gitignore`.
- The attempted `.worktrees/v1-rc-critical-path` creation was denied by the sandbox and then interrupted at the approval prompt.
- No `work/v1-rc-critical-path` branch or worktree was created.

### 2.2 PR #1330

- PR: `merge/rework-descar-into-main` -> `main`
- Authoritative PR head: `b91567fcff9c771b89808eadc71379174a0b259f`
- Base: `main` at `f07a1cb6`
- State observed on 2026-07-22: open and blocked.
- Checks at observation time:
  - `sunset`: pass
  - `python (3.12)`: fail
  - `ui`: fail
  - `gamma-suite (chromium)`: cancelled; current result unknown

Local `merge/rework-descar-into-main` is not authoritative: it points at unpushed `09120a9e`, whose only change skips eight E2E assertions. Do not use it as the execution base.

### 2.3 Known CI Root Causes

Python:

- `tests/api/test_profiles_route.py:106` carries an unused `# noqa: F631`.
- Focused candidate commit `39caaad9` removes only that directive.
- The PR seed data explicitly declares `flm.device_class = "npu"` and `kokoro.device_class = "cpu"`; tests must assert those exact values.
- Do not import `24b278a3` or `fceab946`; those commits encode assumptions from the divergent LLAMA_SET_ROWS branch.

UI:

- `ui/src/dash/model-drawer.jsx`: duplicate `const dirty` declaration. Keep the first declaration and remove the repeated block.
- `ui/src/dash/slot-modals.jsx`: premature `</FieldGroup>` inside an open fragment. Remove the premature close; keep the real outer close.
- Do not cherry-pick `bac16667`: its merged equivalent is already in the ancestry and conflicts with the PR's later drawer rewrite.

Prohibited shortcut:

- Do not use local `09120a9e` or the LLAMA branch's stale-test skip commits.
- A skipped or stale UI test is not RC evidence. Repair it or replace it with a test at the current contract boundary.

### 2.4 Current Execution Update — 2026-07-22

The original integration baseline remains unchanged: PR #1330 head `b91567fcff9c771b89808eadc71379174a0b259f` over `main` at `f07a1cb6c6eaa93e852a3a27e37d7faf63521608`. GitHub still reports PR #1330 open and blocked at that head: `sunset` succeeds, Python and UI fail, and Chromium is cancelled/unknown.

Active work is now split into isolated worktrees:

| Worktree / branch | Current tip | Ownership and state |
|---|---:|---|
| `.worktrees/ci-pr1330-repair` / `fix/pr1330-ci-repair` | `00533630` | Gate 1 repair. Profile-route baseline test fix committed; UI parser repair remains uncommitted/in progress. |
| `.worktrees/hermes-python312-policy` / `feat/hermes-python312-policy` | `f7eaa732` | Hermes Python 3.12 install/provisioning policy implemented and pushed to its branch; generated graph files remain dirty. This branch started from `main`, not the PR head. |
| `.worktrees/v1-rc-critical-path` / `work/v1-rc-critical-path` | `1aafb8a2` | Lean lifecycle design and plan committed. Lifecycle source/tests and release-gate scripts are uncommitted/in progress. This branch started from the PR head. |

The primary checkout remains the dirty divergent `feat/llama-set-rows` checkout. Preserve its uncommitted control documents and agent artifacts; do not use it as an integration base.

Current lifecycle control documents:

- `docs/rework/v1-rc-installer-lifecycle-updated-handoff-2026-07-22.md` (currently in the primary checkout)
- `docs/superpowers/specs/2026-07-22-lean-install-setup-update-design.md` (currently committed in `.worktrees/v1-rc-critical-path`)
- `docs/superpowers/plans/2026-07-22-lean-install-setup-update.md` (currently committed in `.worktrees/v1-rc-critical-path`)
- `.worktrees/v1-rc-critical-path/.superpowers/sdd/progress.md`

Parallel execution is permitted only across isolated worktrees with explicit file ownership and one integration owner. The Hermes and lean-lifecycle branches both touch installation behavior and must receive a deliberate file-level reconciliation; neither may be merged wholesale into the other. Gate 1 remains the integration prerequisite: repair PR #1330, obtain required green CI, land it, then rebase/reconcile later work onto the resulting integration tip.

No RC branch has been merged, no required full CI gate is green, and no live-box RC validation has been accepted.

## 3. Canonical Reading Order

1. This document.
2. `docs/superpowers/specs/2026-07-22-v1-rc-critical-path-design.md`.
3. `docs/rework/v1-rc-installer-lifecycle-updated-handoff-2026-07-22.md`.
4. `docs/superpowers/specs/2026-07-22-lean-install-setup-update-design.md`.
5. `docs/superpowers/plans/2026-07-22-lean-install-setup-update.md`.
6. `docs/rework/handoff-flm-remaining-2026-07-22.md`.
7. `docs/rework/REWORK_BOARD.md` (single-writer board).
8. `docs/rework/REWORK.md`.
9. `docs/rework/REWORK_BOARD_PROTOCOL.md`.
10. `docs/rework/r5-sync-assessment-2026-07-19.md`, especially sections 4-6 and 10.
11. `docs/rework/flm-1.0-rework-plan.md`.
12. `docs/rework/onnx-npu-support-plan.md` (post-v1 design context only).
13. `docs/rework/hal0-specs/` for each owning lane.

Until the lean-lifecycle branch is integrated, reading-order items 4-5 must be read from `.worktrees/v1-rc-critical-path/` at their listed repository-relative paths.

Docs are evidence, not truth. Verify each claim against the PR-head code before dispatching implementation.

## 4. Approved Scope

### 4.1 Included

- PR #1330 branch reconciliation, CI repair, review, and landing readiness.
- R5 section 4: Memory/Admin MCP correctness, auth, catalog, memory plugin, and tests.
- R5 section 5: CLI auth correctness, missing high-value verbs, consolidation, and docs parity.
- R5 section 6: install target, permissions, image/runner lineage, uninstaller cleanup, and both-box validation.
- Full UI parity for backend request/response shape changes.
- UI D4-D5 safety work: security controls and migration UX.
- FLM Phases 1-4, including model catalog and NPU trio editing.
- Capped local gates, required GitHub CI, and live validation evidence.

### 4.2 Deferred

- UI D6 general diagnostics panel.
- Raw ONNX and ONNX Runtime GenAI providers.
- HP voice, automation, context, legacy-suite, and realtime tails already marked post-core.
- Broad router/god-module decomposition unless a touched module blocks safe testing.
- Release tag, publication, and distribution.

## 5. Non-Negotiable Engineering Rules

- PR #1330's exact remote head is the integration source of truth.
- Never merge `feat/llama-set-rows` wholesale.
- Never bypass a failing UI test by skipping it.
- Use test-driven development for behavior changes: red, green, refactor.
- One implementation agent per worktree/branch. Disjoint writers may run in parallel only with explicit file ownership, no shared dirty filesystem, and one integration owner; independent read-only inventories may also run in parallel.
- Each implementation task gets an independent spec/quality review before merge or before any dependent task consumes it.
- The board is single-writer. Agents report row deltas; they do not edit `REWORK_BOARD.md`.
- Every code touch runs the capped gate appropriate to its blast radius.
- Deploy-affecting changes must be validated on halo150 and halo143.
- Never mutate LXC105 during memory migration rehearsal.
- Do not run the full local pytest suite when it can hang on podman/systemd; use targeted suites and GitHub CI as the full gate.
- After code changes, run `graphify update .`.
- Preserve unrelated dirty files and user changes.

## 6. Contract-First Delivery Model

Every affected behavior is completed as one vertical slice:

```text
domain behavior
  -> typed backend request/response
  -> API route and CLI adapter
  -> UI normalization hook
  -> rendered state and mutation flow
  -> backend contract test
  -> UI component/e2e test
  -> live response smoke
```

For each slice, record a contract-matrix row:

| Field | Required content |
|---|---|
| Operation | Stable domain verb and owner |
| Backend | Request/response model and route |
| CLI | Command, output, exit status, refusal behavior |
| UI | Hook/adapter, component, mutation, refresh rule |
| States | loading, empty, partial, unauthorized, unavailable, validation error, success |
| Safety | confirmation, dry run, redaction, rollback |
| Evidence | backend test, UI test, live smoke |

Compatibility belongs in a narrow adapter. Components must not infer meaning from missing fields or stale names.

## 7. Execution Gates And Task Order

### Gate 0 — Isolated Authoritative Workspace (complete)

The following commands are the historical creation recipe. Do not rerun them while `.worktrees/v1-rc-critical-path` and `work/v1-rc-critical-path` exist. The worktree was created from the exact PR head and is now the lean-lifecycle integration worktree.

Creation recipe:

```bash
cd /home/mint/hal0
git worktree add .worktrees/v1-rc-critical-path \
  -b work/v1-rc-critical-path \
  b91567fcff9c771b89808eadc71379174a0b259f
cd .worktrees/v1-rc-critical-path
```

If sandbox approval is required, approve only the scoped `git worktree add` command. Confirm:

```bash
git status --short --branch
git rev-parse HEAD
git merge-base HEAD f07a1cb6
```

Expected HEAD: `b91567fcff9c771b89808eadc71379174a0b259f` and a clean worktree.

### Gate 1 — Repair PR #1330

Task 1A — Python lint and profile contract:

- Remove the unused F631 suppression in `tests/api/test_profiles_route.py`.
- Assert `flm` is `npu` and `kokoro` is `cpu` on the PR baseline.
- Run the route test, Ruff, format check, and import smoke.

Task 1B — UI parser repair:

- Remove the duplicate `dirty` block in `model-drawer.jsx`.
- Remove the premature `FieldGroup` close in `slot-modals.jsx`.
- Run lint and build before behavior changes.

Task 1C — Restore honest gamma coverage:

- Enumerate all `test.skip` calls introduced by the local skip branch.
- Compare each with the actual PR-head UI contract.
- Rewrite obsolete assertions to test the replacement control or backend mutation.
- Delete truly obsolete tests only when the owning contract is covered elsewhere and the task reviewer agrees.
- Run targeted drawer specs, then the full gamma suite in CI.

Task 1D — PR gate:

- Review the complete corrective diff against `b91567fc`.
- Push only after capped local verification.
- Monitor required checks until terminal.
- Do not merge unless all required checks are green.

### Gate 2 — R5 Section 4: Memory/Admin MCP

Execute as separate reviewed slices:

1. Add PATCH support to admin REST dispatch, plus an import-time method guard and route-sync test.
2. Replace raw-bearer journald identity with a principal-derived or hashed label; add defense-in-depth redaction.
3. Provision Hermes/brain MCP clients with service-identity bearer credentials and test auth-on `tools/list`.
4. Complete the Tier-A admin catalog and correct read-only policy classification.
5. Define explicit exclusions for routes that must never be auto-exposed.
6. Implement deny-by-default route-map autogen with SSE/stream/WS exclusions and stable aliases.
7. Rename memory tools to Hindsight vocabulary, implement `reflect`, update both parity-locked copies, prompts, config, and aliases.
8. Resolve dead `/api/mcp` controls by implementing a real lifecycle or hiding unsupported actions from the UI/API catalog.
9. Add route, PATCH, auth, memory dispatch, parity, and provisioner-prompt tests.

Security slices precede catalog expansion. Autogen follows the interim route-sync pin.

### Gate 3 — R5 Section 5: CLI

Execute as separate reviewed slices:

1. Add one authenticated stream helper and route slot logs, doctor logs, chat, and setup through it.
2. Add auth-on smoke coverage for CLI transport behavior.
3. Add `hal0 auth` status/rotate/require commands without creating a second auth implementation.
4. Wire high-value missing verbs in dependency order: slot rename, model default/update/pull cancel, ports, duplicate, board operations, brain chat.
5. Make `model import-backup` import into the SQLite-authoritative registry.
6. Remove the client-side port scan and trust PortAuthority's server result.
7. Make app list/uninstall prefer `/api/services` with a systemd fallback.
8. Sequence FLAGS-own and migration-dependent CLI deprecations after their deploy window.
9. Resolve the updater nightly mismatch explicitly. Default recommendation: one canonical channel enum shared by API and CLI; do not document unsupported flags.
10. Tighten docs-parity tests and correct CLI documentation.

### Gate 4 — R5 Section 6: Installer/Uninstaller

Execute as separate reviewed slices:

1. Ship and enable `hal0.target`, add doctor coverage, and uninstall it cleanly.
2. Add the model-store permission row and install-time ownership repair; validate a default-store pull.
3. Repin ComfyUI only after verifying the cross-repo digest and container paths.
4. Resolve CPU runner lineage. Default recommendation: use the verified CPU image plus a manifest key; record the decision in both repositories.
5. Remove stale root-era comments and resolve the dead first-run-lock surface.
6. Remove Quadlet sources, sudoers grant, bench data, Honcho units/timers, dangling Hermes shim, and other verified generation-old artifacts.
7. Restore a captured foreign Hermes backup where present.
8. Correct purge behavior and documented keep/remove lists.
9. Add install, upgrade, reboot-autostart, uninstall, reinstall, and no-ghost-slot tests/runbook evidence.

### Gate 5 — UI Security And Migration UX

For every Gate 2-4 backend shape change:

1. Inventory the route, response model, frontend hook, and consumer.
2. Add or update a single normalization adapter.
3. Implement loading, empty, unauthorized, unavailable, validation, progress, and terminal states.
4. Preserve actionable backend refusal messages.
5. Add preview/dry-run and explicit confirmation for destructive actions.
6. Ensure credentials and unsanitized logs are never rendered.
7. Add component/e2e coverage using production-shaped fixtures.
8. Add at least one live-backend smoke per surface group.

The D6 general diagnostics panel remains deferred. Do not let D6 become a hidden prerequisite for D4-D5 safety work.

### Gate 6 — FLM 1.0 Rework

Phase 1 — data/config:

- Reconcile the FLM profile's `device_class` rule against the final hardware-ownership spec before editing. The existing FLM plan and PR baseline disagree; the ratified spec governs.
- Populate FLM hardware-grid seed values only if accepted by the current `SlotConfig` contract.
- Sunset-stamp the FLM image environment fallback and preserve the canonical image resolution chain.

Phase 2 — models UI:

- Add the NPU/FLM tab backed by `GET /api/slots/flm/models`.
- Normalize installed/available catalog rows and capability filters.
- Implement pull, progress, refresh, remove, edit, and assign actions against verified routes.
- Use the existing icon library and menu patterns.

Phase 3 — NPU trio editing:

- Add independent Chat, STT, and Embed model selection.
- Make UI field names exactly match the final `NpuConfig` request shape.
- Pass per-role models to the one `flm serve` process.
- Preserve the intentional direct STT/Embed routing through `NpuTrioRouter`.

Phase 4 — quality and evidence:

- Add profile, seed, provider, route, component, and Playwright tests.
- Repair or replace stale NPU drawer tests; do not skip them.
- Document the single-process trio architecture and sunset markers.

### Gate 7 — RC Validation

Local capped gate:

```bash
uv run ruff check src tests
uv run ruff format --check src tests
PYTHONPATH="$PWD/src" uv run python -c "from hal0.api import create_app; print(len(create_app().routes))"
uv run python scripts/check_sunset.py
uv run pytest <targeted-test-files> -q
cd ui
npm run lint
npm run build
npx playwright test <targeted-specs> --reporter=line
```

Then run full required GitHub CI. Local capped tests are not a merge signal by themselves.

Live order:

1. Deploy the bilingual runtime to halo150 and halo143.
2. Exercise Memory/Admin operations and auth refusals.
3. Rehearse memory/config migrations with deterministic fixtures.
4. Run FLAGS migration dry-run before guarded apply.
5. Re-run SLOT-B ID flip on halo143 and verify no name-keyed reseeding or split-brain.
6. Validate install, upgrade, reboot autostart, `doctor perms --fix`, default-store pull, uninstall/reinstall, and no ghost slots on both boxes.
7. Validate FLM catalog, pull/remove progress, and Chat/STT/Embed configuration against the live NPU runtime.
8. Record commands, versions, timestamps, outputs, and rollback evidence under `docs/rework/deploy-validation/`.

## 8. Subagent-Driven Execution Protocol

### 8.1 Role Allocation

- Explorer agents: read-only, parallel, narrowly scoped inventories.
- Implementer agent: one active writer per isolated worktree, owns explicit files and a single task. Multiple writers require disjoint worktrees, explicit ownership, and one integration owner.
- Task reviewer: fresh context, reviews spec compliance and code quality from a diff package.
- Fix agent: addresses the complete reviewer finding set and reruns covering tests.
- Final reviewer: strongest available model, whole-branch review after all tasks.

### 8.2 Model Selection

- Fast/low-cost model: mechanical one- or two-file edits with exact instructions.
- Standard model: multi-file integration, UI/backend wiring, and routine review.
- Strongest model: security, migrations, installer behavior, architecture conflicts, and final review.
- Prefer fewer well-scoped turns over the cheapest per-token model when a task requires judgment.

### 8.3 File-Based Handoffs

Keep large context out of prompts:

- Extract each task into `.superpowers/sdd/task-N-brief.md`.
- Require reports at `.superpowers/sdd/task-N-report.md`.
- Generate a diff package from the task's recorded base commit to its head.
- Give reviewers the brief, report, diff package, and global constraints only.
- Track completion in `.superpowers/sdd/progress.md`.

Each implementer reports only:

```text
STATUS: DONE | DONE_WITH_CONCERNS | NEEDS_CONTEXT | BLOCKED
COMMITS: <hashes>
TESTS: <one-line command/result summary>
CONCERNS: <none or concise list>
```

### 8.4 Per-Task Loop

1. Record task base commit.
2. Write the failing test and verify the expected failure.
3. Implement the minimum behavior.
4. Verify green and run the task's capped gate.
5. Commit only task-owned files.
6. Generate the review package.
7. Run fresh spec/quality review.
8. Fix every Critical/Important finding and re-review.
9. Append completion to the durable ledger.
10. Run `graphify update .` after code changes.

Do not start the next writer task while review findings remain open.

## 9. AFK Operating Rules

Continue without asking between tasks when:

- the action is read-only;
- the task is explicitly in this document;
- edits stay in the isolated worktree;
- tests and review provide deterministic feedback; and
- no live system, release, merge, push, or destructive operation is involved.

Stop and request direction when:

- a plan requirement contradicts current code or a ratified spec;
- a security or migration choice changes externally visible behavior;
- a live box, LXC105, release, merge, push, tag, or publication would be mutated;
- a reviewer finding conflicts with the approved design;
- baseline tests fail for an unrelated reason that cannot be isolated; or
- sandbox approval is required for a new category of external write.

## 10. Decisions And Defaults

| Decision | Current handling |
|---|---|
| Release endpoint | Validated v1.0.0 RC; no tag/publication |
| UI D4-D6 | D4-D5 included; D6 diagnostics deferred |
| Updater nightly channel | Resolve to one shared API/CLI enum; verify before implementation |
| CPU runner lineage | Recommend verified CPU image + manifest key; requires explicit cross-repo confirmation |
| ComfyUI host-network loopback | Do not change implicitly; preserve as a decision gate |
| HP voice/automation/context | Deferred unless user re-promotes |
| God-module LOC burn-down | Track touched-module risk; no broad refactor in RC |
| FLM/profile ownership | Profiles remain device-agnostic logical tuning templates; `npu` is the physical device class and runtime selection belongs to the slot/runner. Gate 1 may test the PR baseline truth without making that transitional state the final FLM design. |
| FLM role lifecycle | One FLM slot/process owns independently enableable Chat, STT, and Embed role bindings. Disabled roles retain their model selection; an all-disabled slot stays configured but does not launch or claim resources. Design/spec update still pending. |
| NPU execution modes | Default automatically from runner/model metadata; operators may choose only runtime-declared validated modes such as `npu-only`, `npu-cpu`, or `npu-igpu`. Resource conflicts require an explicit confirmed owner handoff. Raw ONNX/OGA remain post-v1.0. |

## 11. Durable Progress Ledger Template

The original gate-level template remains below for recovery context. The live file at `.worktrees/v1-rc-critical-path/.superpowers/sdd/progress.md` now tracks the lean lifecycle plan and must not be replaced with this template. Gate-level status is recorded in section 2.4 and should be refreshed whenever an owned branch is reviewed or integrated.

```markdown
# SDD Progress Ledger — v1.0.0 RC Critical Path

Plan: docs/rework/v1-rc-critical-path-handoff-plan-execution.md
Baseline: b91567fc

- [ ] Gate 0: isolated workspace
- [ ] Gate 1: PR #1330 CI repair
- [ ] Gate 2: Memory/Admin MCP
- [ ] Gate 3: CLI
- [ ] Gate 4: Installer/Uninstaller
- [ ] Gate 5: UI security and migration UX
- [ ] Gate 6: FLM rework
- [ ] Gate 7: RC validation

Task N: complete (commits <base7>..<head7>, review clean, tests <summary>)
```

Trust this ledger and git history after context compaction. Never redispatch a completed task.

## 12. Resume Checklist

```bash
cd /home/mint/hal0

# Inspect active ownership before touching any worktree.
git worktree list --porcelain
git -C .worktrees/ci-pr1330-repair status --short --branch
git -C .worktrees/hermes-python312-policy status --short --branch
git -C .worktrees/v1-rc-critical-path status --short --branch

# Confirm authoritative baseline and current task tips.
git rev-parse b91567fcff9c771b89808eadc71379174a0b259f
git -C .worktrees/ci-pr1330-repair log -4 --oneline
git -C .worktrees/hermes-python312-policy log -4 --oneline
git -C .worktrees/v1-rc-critical-path log -4 --oneline

# Read current control documents and durable progress.
cat docs/rework/v1-rc-critical-path-handoff-plan-execution.md
cat docs/rework/v1-rc-installer-lifecycle-updated-handoff-2026-07-22.md
cat .worktrees/v1-rc-critical-path/.superpowers/sdd/progress.md

# Resume only the worktree/file set explicitly assigned to this session.
# Do not import feat/llama-set-rows or the stale skip commit.
```

Before any completion claim, consult Shepherd history, inspect the complete diff, run fresh verification, and report actual failures as failures.

## 13. Current Handoff Status — 2026-07-22

- The approved umbrella design and this execution plan exist but remain uncommitted in the dirty primary checkout.
- Gate 0 is complete.
- Gate 1 is active in `fix/pr1330-ci-repair`; its profile test correction is committed and its UI parser repair is still in progress. PR #1330 itself remains unchanged, open, and blocked until reviewed repairs are pushed and required CI is green.
- Hermes Python 3.12 install/provisioning policy is committed and pushed on `feat/hermes-python312-policy`, based on `main`; it requires later reconciliation with the PR-head integration line.
- Lean lifecycle design and plan are committed on `work/v1-rc-critical-path`; lifecycle implementation and release-gate scripts are in progress and uncommitted.
- The Hermes and lean-lifecycle lanes overlap installation behavior. Reconcile exact files and contracts after Gate 1 rather than merging either branch wholesale.
- NPU/FLM ownership decisions have been clarified in section 10, but the design/spec and implementation plan are not yet updated or approved.
- Gates 5-7 remain unaccepted. No full required CI result, integration merge, live-box RC evidence, tag, publication, or distribution has occurred.
- Immediate integration priority: finish and independently review Gate 1, run capped local verification, update the authoritative PR branch, require terminal green CI, then land PR #1330 before rebasing/reconciling dependent branches.
