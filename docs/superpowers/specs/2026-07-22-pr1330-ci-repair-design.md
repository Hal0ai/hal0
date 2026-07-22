# PR #1330 CI Repair Design

## Objective

Restore honest CI signal for the authoritative PR #1330 head `b91567fcff9c771b89808eadc71379174a0b259f` with the smallest defensible patch. The repair must not import changes from divergent branches, skip failing tests, or overlap active Hermes and installer/update/uninstall work.

## Baseline and workspace

- Base commit: `b91567fcff9c771b89808eadc71379174a0b259f`
- Isolated branch: `fix/pr1330-ci-repair`
- Isolated worktree: `/home/mint/hal0/.worktrees/ci-pr1330-repair`
- Main baseline for ancestry checks: `f07a1cb6c6eaa93e852a3a27e37d7faf63521608`

The dirty primary checkout remains untouched. Generated `graphify-out` changes are not part of the repair.

## Repair scope

The first slice repairs only the known PR failures:

1. Remove the obsolete Ruff suppression in `tests/api/test_profiles_route.py` and align the FLM/Kokoro assertions with the seed behavior present at the authoritative PR head.
2. Remove the duplicate `dirty` declaration in `ui/src/dash/model-drawer.jsx`.
3. Correct the malformed `FieldGroup` nesting in `ui/src/dash/slot-modals.jsx`.
4. Run the existing Gamma/Playwright coverage honestly. If a test no longer matches the repaired UI, rewrite only that test to exercise the current replacement control; never skip it as a substitute for acceptance evidence.

Each failure must be reproduced before its fix. Changes remain minimal and attributable to a reproduced failure.

## Explicit exclusions

This slice will not touch:

- CLI implementation or CLI overhaul files.
- `installer/**`, updater implementation, uninstall logic, installer lifecycle tests, or setup/preflight behavior.
- Hermes provisioning, prompts, agent configuration, credentials, or related tests.
- Release/nightly workflows, live systems, deployment, merging, pushing, or `REWORK_BOARD.md`.
- The prohibited local skip commit `09120a9e` or any equivalent reduction in Gamma coverage.
- Broad refactors or behavior changes unrelated to the four known failure areas.

If verification requires a file in an excluded area, work stops for ownership reconciliation.

## Verification strategy

Verification proceeds from focused evidence to broader gates:

1. Confirm HEAD and clean ownership boundaries.
2. Reproduce the focused Python profile-route failure and Ruff finding using an isolated `HAL0_HOME` where needed.
3. Reproduce the UI parser failures with the baseline lint/build commands.
4. After each fix, rerun the narrow failing command.
5. Run Python Ruff check, Ruff format check, and targeted profile-route pytest.
6. Run UI lint and production build.
7. Run currently ungated UI typecheck and unit tests as diagnostic evidence; failures do not expand this repair automatically.
8. Run targeted Chromium tests first, followed by the required Gamma workflow-equivalent command when locally feasible.
9. Inspect the final diff and confirm no excluded paths or new unconditional skips appear.
10. Run `graphify update .` after source changes, keeping generated outputs out of the repair commit unless explicitly required by repository policy.

A cancelled Chromium run is reported as unknown, not passing. Full required GitHub CI remains the final merge gate.

## Follow-up CI hardening

Only after the baseline repair is verified, a separate design and approval step may consider:

- A ratchet preventing newly introduced unconditional Playwright skips.
- Adding `npm run typecheck` and `npm run test:unit` to required UI CI if both are clean at the repaired baseline.
- Better artifact retention for failed, timed-out, or cancelled Playwright runs.

Mypy gating, Python marker repartitioning, nightly/release changes, and installer lifecycle CI remain separate workstreams.

## Stop conditions

Stop and return to the integration owner if:

- The authoritative PR head or integration tip moves and cannot be reconciled safely.
- A required edit overlaps Hermes or installer/update/uninstall ownership.
- The known failure cannot be reproduced or evidence contradicts this design.
- Gamma correctness would require skipping coverage or inventing behavior.
- An unrelated verification failure cannot be isolated without broadening scope.

## Approved slot-modal restoration amendment

### Root cause

Commit `51be6cf7` changed `ui/src/dash/slot-modals.jsx` by 1,612 lines while implementing seeded profiles. Its parent, `6af8759b`, has the same slot-modal blob as current `main` (`f07a1cb6`). The rewrite introduced the intended dedicated Hardware field group, but also accidentally replaced newer drawer code with stale code. In particular, it removed the rendered per-slot `extra_args` editor, the NPU Chat/ASR/Embed capability matrix, and newer Reasoning/MTP behavior already present on `main`.

Commit `4a6ba552` removes duplicated malformed JSX and restores parsing, but parser success alone is insufficient: focused Chromium evidence still reports four failures because `extra_args` is no longer rendered, plus two stale `ctx_size` selectors. The NPU regression is not covered by those failing tests.

### Restoration strategy

Use the known-good `6af8759b`/current-`main` slot modal as the behavioral reference. Restore its current drawer behavior, then reapply only the PR's intended Hardware ownership change:

- Keep one Slot group for instance identity.
- Keep one Hardware group containing exactly one device, NGL, threads, binary, fit-warning, and image-pin control.
- Preserve the current-main Model, `extra_args`, NPU Chat/ASR/Embed, Reasoning, MTP, Inference, and Advanced behavior.
- Preserve the PR's existing request construction and validation unless the reference behavior and a failing test prove it was stale-overwritten.
- Do not import unrelated changes or use test skips.

Implement this as an additive corrective commit; do not rewrite or reset existing branch history.

### Regression evidence

Before restoration, retain the existing focused failures as red evidence. After restoration:

1. Rewrite only stale `ctx_size` selectors to target the current `Context` replacement control.
2. Add focused coverage proving each Hardware test ID occurs exactly once.
3. Add focused NPU coverage proving Chat, ASR, and Embed controls remain rendered and usable while the generic non-NPU Model group stays hidden.
4. Run lint, production build, typecheck, unit tests, focused Chromium, and the workflow-equivalent Gamma command.
5. Stop rather than weaken coverage if restored current-main behavior conflicts with the approved hardware-ownership contract.

### Non-goals

This amendment does not redesign the slot drawer, implement the future FLM 1.0 role-binding design, alter backend payload contracts, or broaden the repair into installer, updater, Hermes, release, or lifecycle work.
