---
name: hal0-contributing
description: "House rules for any agent working on Hal0 (Hal0ai/hal0 and sibling repos): issue labels, filing rules, verification, DCO, test tiers, ADRs, and the review and merge pipeline. Use before filing, triaging, implementing, reviewing, or merging anything in a Hal0 repository."
---


# Contributing to Hal0 as an agent

These rules come from `CLAUDE.md`, `CONTRIBUTING.md`, and `ARCHITECTURE.md` in Hal0ai/hal0. When this skill and those files disagree, the repository files win; re-read them.

## Verify before you write

Every CLI flag, config key, endpoint, path, and default has exactly one authority under `src/hal0/`. Docs and comments go stale against it. Check the source, not memory, and cite what you verified as `file:line`, a commit SHA, or a CHANGELOG entry.

## GitHub issue labels (three axes)

- **Status — exactly one per open issue:** `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. Never invent new status labels.
- **Priority — exactly one once triaged:** `P0` breaks a feature, loses data or state, or exposes security or privacy (current patch milestone). `P1` is wrong or confusing for a user (next minor). `P2` is nice to have (`Backlog`).
- **Type — optional:** `bug`, `enhancement`, `documentation`.

`ready-for-agent` means an agent can fix it unsupervised with unit tests, without hardware or a design decision, and the change is small.

## Filing rules

1. One defect per issue. A sweep is one tracking issue with a checklist.
2. State the impact if nobody fixes it, the reproduction or `file:line`, and a fix-size guess from XS to L. Use the forms in `.github/ISSUE_TEMPLATE/`.
3. Do not file purely cosmetic findings unless the same session fixes them.
4. Search for duplicates first (`gh issue list -R Hal0ai/hal0 --search "<terms>" --state all`).

## Commits and branches

- One concern per branch (`feat/`, `fix/`, `chore/`, `docs/`, `refactor/`), one worktree per branch under `/mnt/mintdev/worktrees/<project>/`.
- Conventional Commits, one logical change each, with a DCO sign-off (`git commit -s`).
- Run the test tier `CONTRIBUTING.md` names for the area you touched before committing. Report the command and the result in the PR body.
- `docs/.devdocs/` and `docs/superpowers/` are gitignored and local-only. Never commit them, and never cite them as if a reader can open them.

## Architecture decisions

Read the relevant record in `docs/adr/` before changing behaviour it covers. Add a new ADR instead of quietly diverging.

## Review and merge pipeline (Hal0 squad)

1. The implementer opens one PR per branch and links it on the Multica issue.
2. **Hal0 Reviewer** reviews every PR. Pi-authored PRs are never merged without that review.
3. **Hal0 Orchestrator** makes the merge decision (`gh pr merge <n> --squash --auto --delete-branch`).
4. PRs that touch the installer, `.github/workflows/`, release tooling, runner-image pins, credentials, or migrations are **never auto-merged**. They are handed to Alex.
5. Do not wait on CI inside a run. Hand off with the PR link and local test results.

## Local hardware etiquette

The hal0 box (Ryzen AI MAX+ 395, Strix Halo, XDNA2 NPU) serves real slots. Never load, unload, or retune a serving slot without either owning that work (Bench & Tuning, Box Operator) or a sub-issue that says so. The `brain` slot belongs to Hindsight consolidation; agents do not use it.
