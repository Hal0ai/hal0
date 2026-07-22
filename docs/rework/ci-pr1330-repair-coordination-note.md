# Coordination Note — PR #1330 CI Repair vs Lifecycle Overhaul

> Date: 2026-07-22
>
> CI repair owner/session: `/ci-pr1330-repair/`
>
> Lifecycle design worktree: `/home/mint/hal0/.worktrees/v1-rc-critical-path`
>
> Lifecycle design baseline: `b91567fcff9c771b89808eadc71379174a0b259f`

## Full lifecycle design

Read:

`/home/mint/hal0/.worktrees/v1-rc-critical-path/docs/superpowers/specs/2026-07-22-lean-install-setup-update-design.md`

The design is still awaiting final written-spec review. Do not implement its CI changes from the CI-repair session.

## Ownership rule

The `/ci-pr1330-repair/` session owns existing PR #1330 CI repair and cleanup. The lifecycle-overhaul session will not edit CI workflow/configuration or perform broad existing-test cleanup until that work is complete and integrated.

The lifecycle implementation must later rebase/reconcile against the CI repair and preserve its fixes.

## What the CI repair should optimize now

- Restore honest required PR checks.
- Fix current failures rather than skipping behavior coverage.
- Remove existing duplicate/stale tests only where replacement coverage is already present.
- Avoid introducing lifecycle-overhaul jobs or speculative distro matrices.
- Keep changes scoped to the current PR #1330 contract.

## Handoff requested from the CI repair

Please report:

1. final commit/head integrated or ready to integrate;
2. workflows and required job names changed;
3. tests/fixtures deleted, consolidated, or replaced and their replacement coverage;
4. current required versus optional/scheduled job split;
5. measured or approximate required-PR duration after cleanup;
6. remaining known flaky, skipped, duplicated, or disproportionately expensive suites;
7. assumptions the lifecycle overhaul must preserve;
8. exact verification commands and latest terminal results.

## Future lifecycle CI direction — do not implement yet

After the CI repair lands, the lifecycle overhaul intends to use:

- a fast required catalog/resolver/converger contract suite;
- a small representative package-manager matrix (`apt`, `dnf`, `pacman`) rather than every distro/environment/device combination;
- targeted or scheduled GPU/NPU/ROCmFPX/WSL jobs;
- full halo150/halo143 and WSL2 lifecycle validation as a release gate, not per-commit Cartesian-product CI;
- deletion of shallow tests only in the same slice that removes their duplicated production owner.

A new required job should exist only for a distinct failure domain that cannot fit an existing job.
