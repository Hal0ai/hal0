# Preview Release 1.0

## Integration status

- Rebased `work/v1-rc-critical-path` onto current `main`.
- Dropped obsolete slot/model drawer repair commits.
- Confirmed `ui/src/dash/model-drawer.jsx` and `ui/src/dash/slot-modals.jsx` have no diff from `main`.
- Preserved lifecycle, package, and profile work.

## UI coverage and fix

- Updated stale UI tests to match the current controls.
- Fixed a shared-row binding bug that rendered the Kokoro info popover as literal `{sub}`.
- Targeted Chromium UI suite: **42 passed, 3 skipped**.

## Pull-request stack under review

This document reviews the open release-adjacent pull requests reported on 2026-07-22. They have independent merge state and must not be treated as one already-green release candidate.

| PR | Title | Scope | Release assessment |
| --- | --- | --- | --- |
| [#1341](https://github.com/Hal0ai/hal0/pull/1341) | `feat: complete 1.0 seeded profiles and lifecycle catalog` | Lifecycle, seeds, profiles, migration, UI coverage | Primary 1.0 feature PR; currently draft. |
| [#1342](https://github.com/Hal0ai/hal0/pull/1342) | `chore(graphify): narrow tracked graph artifacts` | Generated repository artifacts | No runtime change, but very large repository/review impact. |
| [#1343](https://github.com/Hal0ai/hal0/pull/1343) | `chore(hermes): clean up Python 3.12 resolver tests` | Hermes cleanup and test coverage | Low-risk follow-up to the already-merged Python 3.12 policy. |
| [#1344](https://github.com/Hal0ai/hal0/pull/1344) | `docs: keep development notes out of user docs` | Documentation retention and release-tarball content | No runtime code, but high provenance and release-documentation impact. |

Generated Shepherd and local Graphify working files are intentionally unstaged in this branch. The #1342 review below addresses its separately proposed tracked-artifact policy.

## PR #1341 — complete 1.0 seeded profiles and lifecycle catalog

### Commit groups and implementation diff

- **Slot identity migration:** adds bilingual name/ID slot layout and the offline `hal0 slot migrate-id-keying` command.
- **Seed/profile/Brain defaults:** reworks the profile catalog, static seed slots, ports, and Brain tool routing; follow-up fixes restore runtime-specific `device_class` and align reranking/profile expectations.
- **Lifecycle catalog and resolver:** adds the immutable authored lifecycle catalog, compiled runtime artifact, package reconciliation, host-compatibility resolution, comparison logic, and the final consolidated ROCmFPX Brain pin.
- **Release and CI guardrails:** adds catalog/release validation and tokenized sunset-scar exemptions.
- **UI and coverage alignment:** retains `main`'s drawer implementations, fixes the shared settings-row info binding, and updates affected E2E assertions.

### Functional changes

#### Stable slot identity and migration

- Slot/config/CLI seams support both legacy `<name>.toml` and migrated `<id>.toml` layouts; display names remain separate from stable IDs.
- `hal0 slot migrate-id-keying` moves TOMLs, state, units, and containers to IDs. It takes a backup and requires API/slots to be stopped.
- Legacy name-keyed deployments remain supported until an explicit migration. The migration is intended to be idempotent and roll-forward safe, but it is operationally destructive.

#### Profiles and fresh-install seeds

- The catalog contains 16 profiles: generic workload templates plus Brain, Chadrock dense/MoE, thinking, and coding profiles.
- Slot ownership is explicit: NGL, threads, binary, and image pin are slot-owned rather than profile flags. Five runtime-specific profiles retain `device_class` (`cpu-chat`, FLM, Kokoro, Qwen TTS, and ComfyUI).
- Static seeds expand to 10 slots, adding `coder` and `embed` and making `qwen3tts` a real seed. Fresh-install corrections set TTS to port `8085` and rerank to `8086`.
- Agent remains disabled and model-less, with `chadrock-moe` selected. Brain ships enabled with MiniCPM5 and its tool-model default set to `hal0/agent`.
- Family recipes move into profiles and `family_defaults.toml` is cleared. This removes the former Gemma family override behavior.
- Seed materialization is additive/non-destructive: new seed files and corrected ports apply to fresh or missing slots, not existing operator configurations.

#### Lifecycle catalog, package inventory, and resolution

- `src/hal0/lifecycle/` introduces authored TOML catalogs and a compiled `catalog.json`; runtime consumes the bundled JSON rather than discovering remote state.
- Pydantic validation enforces immutable model/image pins, referential integrity, unambiguous defaults, model/runner format allowlists, prompt contracts, and bootstrap policy.
- The runner registry is catalog-derived, preserves the public runner API, adds stock Vulkan, removes the silent hard-coded fallback, and preserves the ROCmFPX/VulkanFPX facade-level shared-backend override.
- Selection is deterministic across host label, backend/device class, architecture, capability, active state, model format/allowlist, priority, and runner-ID tie-break.
- Unsupported ROCmFPX artifacts cannot select stock llama runners. CPU Brain resolution falls back from ROCmFPX to stock GGUF.
- `compare()` preserves existing operator runner pins and only ensures missing initial/bootstrap slots.
- Fresh install resolves only the Agent slot/default runner. Hermes Brain model selection follows ROCmFPX → stock GGUF → MiniCPM fallback.
- The Brain model is pinned to `Hal0ai/hal0-brain-sft-ROCmFPX-GGUF` at revision `29b846147f30fc631e163a493e9d3d537df474a9`.

#### UI

- `ui/src/dash/model-drawer.jsx` and `ui/src/dash/slot-modals.jsx` are deliberately byte-aligned with current `main`; this PR does not restore older drawer copies.
- `ui/src/dash/settings/shared/SRow.jsx` fixes `FieldInfoIcon` to receive the actual `sub` value, rather than literal `"{sub}"`. This restores the Kokoro help-popover text.
- Drawer E2E tests now target the current Model/HW field grouping and click-to-open information popovers.

### Tests, checks, and evidence recorded

| Surface | Recorded evidence |
| --- | --- |
| Lifecycle/profile focused Python | `239 passed` before integration; rebase-focused lifecycle check later recorded `115 passed`. |
| Lifecycle/resolver | `94 passed`; coverage includes host/backend/device mismatch, priority, determinism, serialization, fallback, and pin preservation. |
| Local release/catalog checks | Ruff, formatting, sunset/catalog checks, compiler/bundled validation, and `scripts/release-check.sh --local` were recorded green during integration. |
| UI build quality | Lint, TypeScript check, unit tests, and production build passed during integration. |
| Targeted UI regression suite | Chromium: **42 passed, 3 intentionally skipped** after the rebase and current-control test alignment. |
| Migration coverage | Unit tests cover artifact moves, backup, stopped-service gate, dry run, partial roll-forward, and idempotence using recording doubles. |

This is historical/local evidence gathered while integrating the branch. A current-HEAD full release-gate/remote-CI result must be captured before tagging.

### Release risks and required decisions

1. **Package catalog gate:** authenticated organization package coverage was previously blocked by a token lacking `read:packages`. Run `check-package-catalog.py` with the release credential and record the outcome.
2. **Final external artifact verification:** re-verify the final consolidated ROCmFPX source/revision, file hash, tensor format, and runner-rejection behavior. Earlier evidence referenced a predecessor source/revision.
3. **Operational convergence:** the resolver/catalog is well tested, but confirm install/setup/update paths consume it as intended; `compare()` deliberately only creates missing bootstrap slots.
4. **Fresh-install smoke:** verify profile template flags materialize through the first-run/static-seed path, not only through the drawer path.
5. **Brain tool routing:** default tool routing selects an Agent seed that is disabled/model-less by default. Define and test expected readiness/read-only degradation and the enabled-Agent path.
6. **Migration rehearsal:** run a real stopped-service/systemd/Podman migration plus rollback rehearsal. Unit doubles are not on-host evidence.
7. **Final gate:** rerun full release checks, current-HEAD backend/UI suites, required image-pull/hardware smoke, and required GitHub CI before marking #1341 ready.

## PR #1342 — narrow tracked Graphify artifacts

### Diff and effect

- Changes `.gitignore` so only `graphify-out/GRAPH_REPORT.md`, `graphify-out/graph.json`, and `graphify-out/analysis/**` remain tracked; other Graphify output is ignored.
- Adds `graphify-out/graph.json` with approximately **833,679 inserted lines**.
- Removes tracked `graphify-out/manifest.json` and 1,396 wiki-export files (approximately **67,865 deleted lines**).
- Does not change application code, tests, packaging logic, or release behavior.

### Evidence and risks

- `git check-ignore --no-index -v` was used to verify that manifest/wiki/cache are ignored and that the selected report/graph/analysis files remain tracked.
- No test suite is needed for runtime behavior, but no final CI evidence was captured.
- **Primary review concern:** committing an 833k-line generated graph may increase clone, diff, and merge costs and can conflict with the stated goal of reducing Graphify churn.
- Rebase stale branches after this policy lands; they may otherwise reintroduce `manifest.json` or wiki outputs.
- Merge before future Graphify-producing work only if the repository intentionally accepts the raw graph JSON as a long-lived tracked release artifact.

## PR #1343 — Hermes Python 3.12 resolver cleanup

### Diff and effect

- Corrects stale 3.11–3.13/range wording to exact Python 3.12 wording in Hermes provisioning docs/comments.
- Removes a redundant `target.mkdir()` before standard `python -m venv` creation.
- Deduplicates/renames resolver coverage and parameterizes venv replacement tests for existing Python 3.11, 3.13, and 3.14 environments.
- Does not intentionally alter Hermes policy, installer output, packaging, manifests, or release artifacts.

### Evidence and risks

- Ruff check/format passed; focused resolver/venv tests recorded **10 passed**; the full Hermes provisioning file recorded **114 passed**; a combined Hermes/config run recorded **125 passed**.
- This follows the already-merged Python 3.12 policy and should merge only after that base (already structurally satisfied).
- Residual risk is low: the removed directory creation is exercised with mocked runners rather than a real venv subprocess, though CPython `venv` supports creating the target directory.

## PR #1344 — keep development notes out of user docs

### Diff and release-artifact effect

- Adds `docs/.development/` to `.gitignore` and rewrites `docs/README.md` to define the user-facing documentation surface.
- Deletes tracked `docs/archive`, `docs/design`, `docs/internal`, `docs/issues`, `docs/plans`, `docs/rework`, and `docs/superpowers` content: **104 files, +22 / −32,859**.
- The removed content is only an ignored local move; it is no longer retained in Git history after future pruning (though existing history remains accessible).
- There is no runtime code change. However, `.github/workflows/release.yml` copies `docs/` into signed release tarballs, so these documents disappear from release installations. Wheel contents are unaffected.

### Evidence and risks

- Ignore rules and remaining-documentation references were scanned; `git diff --check` passed. No test/build evidence or final green CI was captured.
- **High review concern:** this removes implementation specs, deployment runbooks, audits, and release-manifest documentation from the versioned release surface. Decide whether an accessible, versioned archive or redirects are required before 1.0.
- Known stale references outside `docs/` include `installer/install.sh`, `installer/bench/window.toml`, `scripts/migrate-qwen3tts-to-slot.sh`, and release-workflow comments.
- Merge last among work that touches these paths—especially after documentation-producing PRs such as #1330—otherwise conflicts can restore deleted content. Require rebase, current CI, and an explicit retention plan.

## Release review checklist

- [ ] Decide whether #1342's tracked 833k-line `graph.json` is an acceptable repository/release artifact.
- [ ] Decide whether #1344's deleted engineering documentation needs a versioned archive, redirects, or an exception for release runbooks.
- [ ] Rebase all open PRs onto current `main`; collect final required CI, not only local/in-progress evidence.
- [ ] Run #1341's authenticated package-catalog gate and revalidate the final ROCmFPX artifact pin.
- [ ] Perform #1341 fresh-install, setup/update, Brain routing, and on-host ID-migration smoke tests.
- [ ] Run final full release, backend, UI, and hardware/image-pull checks from the merged release candidate.
- [ ] Mark #1341 ready only after the above gates are green and the release decisions for #1342/#1344 are documented.
