# Preview Release 1.0 — Re-evaluated Release Review

> Re-evaluated against the current PR heads and `main` on 2026-07-22. This document supersedes the earlier integration-history summary. It distinguishes historical branch work from what is currently eligible to ship.

## Release disposition

| PR | Current status | Preview disposition |
| --- | --- | --- |
| [#1341](https://github.com/Hal0ai/hal0/pull/1341) — lifecycle catalog/profile work | Closed; superseded/deferred | **Do not reopen or merge as-is.** Rebuild any required slice from current `main`. |
| [#1342](https://github.com/Hal0ai/hal0/pull/1342) — Graphify tracking | Closed, unmerged, conflicting with newer `main` | **Exclude.** Revisit only as a narrow generated-artifact policy PR. |
| [#1343](https://github.com/Hal0ai/hal0/pull/1343) — Hermes Python 3.12 cleanup | Squash-merged as `4968f023` | **Included through `main`.** No further merge action. |
| [#1344](https://github.com/Hal0ai/hal0/pull/1344) — development-doc deletion | Closed, unmerged; stale against newer documentation | **Exclude.** Preserve versioned engineering documentation. |

**Release baseline:** cut Preview from current `main`, not from #1341's historical branch. Any lifecycle work needed for Preview must be selected deliberately and rebuilt on current `main` with fresh validation.

## #1341 — lifecycle catalog and seeded-profile branch

### What the branch contained

The branch combined several substantial initiatives:

- Stable slot-ID migration with legacy name-key compatibility and `hal0 slot migrate-id-keying`.
- 16 profiles and 10 static slot seeds; slot-owned hardware fields; corrected TTS/rerank ports; Brain and Agent default changes.
- Authored immutable lifecycle TOML, compiled catalog JSON, catalog-derived runner registry, deterministic host/model/runner resolution, package inventory, and release checks.
- ROCmFPX Brain pin to `Hal0ai/hal0-brain-sft-ROCmFPX-GGUF@29b846147f30fc631e163a493e9d3d537df474a9`.
- Test/docs/CI work, plus historical UI-test alignment. Drawer source and the shared SRow binding are already represented in current `main` and are not a lifecycle release blocker.

### Why it is not a release candidate

- Against current `main`, **27 commits are patch-equivalent and only 23 are unique**. The branch therefore overstates its unique deliverable and carries stale/broad history.
- The lifecycle catalog is not yet the operational authority for install/setup/update reconciliation. Runtime use is limited to deriving the runner registry; `resolve()` and `compare()` otherwise have test-only consumers.
- Bootstrap policy and shipped static seeds conflict:
  - lifecycle bootstrap says **Agent enabled / Brain disabled**;
  - static seeds say **Agent disabled and model-less / Brain enabled**.
- Brain tool routing defaults to `hal0/agent`, but the shipped Agent seed is disabled/model-less. The fresh-install tool-turn path lacks a proven graceful-degradation contract.
- Immutable-pin syntax is checked, but authenticated package coverage and final model/image provenance remain incomplete.
- The slot-ID migration is covered with doubles, not a real stopped-service, systemd, Podman, backup, roll-forward, and rollback rehearsal.

### Required conditions before extracting any lifecycle slice

1. Build a narrow branch from current `main`; do not revive #1341 wholesale.
2. Make one authority responsible for install/setup/update convergence, and prove it consumes the catalog/resolver.
3. Align lifecycle bootstrap policy with static-seed enablement and model defaults.
4. Test Brain tool behavior with Agent unavailable and with Agent enabled; neither path may dead-end or return an unhandled default failure.
5. Use release credentials to run package-catalog coverage and verify final model/image provenance, artifact hash/format, runner compatibility/rejection, and image pulls.
6. If shipping ID migration, perform a real stopped-service migration plus rollback rehearsal.
7. Run current-HEAD release, backend, UI, fresh-install, update, and required hardware/image-pull checks on the resulting narrow candidate.

### Historical evidence (useful but not release acceptance)

Earlier branch integration recorded deterministic-resolution tests, focused lifecycle/profile tests, local catalog/release checks, UI lint/typecheck/build, and targeted Chromium E2E coverage (**42 passed, 3 intentionally skipped**). These results do not validate the current main-based release candidate and must be rerun for any extracted work.

## #1342 — Graphify artifact tracking

### Diff

- Retains tracking for `graphify-out/GRAPH_REPORT.md`, `graphify-out/graph.json`, and `graphify-out/analysis/**`; ignores other Graphify output.
- Adds a generated `graph.json` of about **833,679 lines**.
- Removes tracked `manifest.json` and 1,396 wiki-export files (about **67,865 deleted lines**).

### Assessment

- No application-runtime or official staged-tarball behavior changes.
- It does materially affect source archives, clone size, review cost, merge conflict risk, and generated-artifact policy.
- Captured CI had Python/UI/sunset passing, but the γ suite failed.
- Keep it closed. If revisited, make it policy-only and leave raw regenerable `graph.json` untracked unless there is an explicit long-term reason to version it.

## #1343 — Hermes Python 3.12 resolver cleanup

### Diff and status

- Already merged to `main` as `4968f023`.
- Final diff is two files, **+17/−40**: exact-3.12 wording corrections, duplicate/misnamed test cleanup, parameterized venv replacement coverage for existing Python 3.11/3.13/3.14 environments, and removal of a redundant target-directory creation before `python -m venv`.

### Assessment

- The venv-directory change is a small implementation behavior change, not merely comment/test cleanup; standard CPython `venv` is expected to create the target directory.
- Recorded evidence: Ruff check/format, focused resolver/venv tests (**10 passed**), full Hermes provisioning tests (**114 passed**), and combined Hermes/config tests (**125 passed**).
- Include through `main`; run normal final release-candidate CI because refreshed remote checks were still queued/in progress at administrative merge time.

## #1344 — development documentation deletion

### Diff

- Adds `docs/.development/` to `.gitignore` and narrows `docs/README.md` to user-facing documentation.
- Deletes tracked `docs/archive`, `design`, `internal`, `issues`, `plans`, `rework`, and `superpowers`: **104 files, +22/−32,859**.
- Release workflow copies `docs/` into signed tarballs, so this deletion would remove those materials from release installations. Wheel contents are unaffected.

### Assessment

- No runtime change, but high documentation/provenance impact.
- Ordinary Git deletion does **not** erase the files from prior reachable commits; it removes them only from future tips/releases unless history is rewritten.
- The branch is stale, and its captured CI had Python/UI/sunset passing but γ-suite failure.
- Keep it closed. Prefer retaining engineering documents in Git and explicitly allowlisting only user-facing documentation when staging release tarballs. Fix stale references before any future documentation reorganization.

## Final Preview release checklist

- [ ] Release from current `main`; do not use #1341 as the candidate base.
- [ ] Confirm #1342 and #1344 remain excluded, unless separately rebuilt and approved.
- [ ] Run final required CI and release checks on the exact release commit.
- [ ] Verify the merged Hermes 3.12 policy on a clean installation/upgrade path.
- [ ] If lifecycle work is required, satisfy all #1341 extraction conditions before reintroducing it.
- [ ] Record artifact, image-pull, and hardware-smoke evidence alongside the final tag decision.
