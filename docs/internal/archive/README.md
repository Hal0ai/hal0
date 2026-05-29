# Internal docs archive

This directory holds planning documents, spike findings, and one-shot
runbooks whose work has shipped and which would otherwise add noise to
`docs/internal/`. The files are kept (not deleted) because they
record decision context that may be useful to future readers diagnosing
why a system was built the way it is.

## What lives here

| Subject | Files | Shipped as |
|---|---|---|
| Phase 8 / Agents v0.3 audit | `audit-2026-05-22-phase8-skill-review.md` | v0.3 Hermes integration (PRs #393–#408) |
| Dashboard v2 implementation plan | `dashboard-v2-implementation-plan-2026-05-23.md` | superseded by v3 dashboard (PR #199 + #364 + #368) |
| Hermes bootstrap plan | `hermes-bootstrap-plan-2026-05-23.md` | PRs #393, #396, #316 |
| Hermes env-probe recipes | `hermes-env-probe-recipes-2026-05-23.md` | integrated into provisioner |
| Hermes upstream map | `hermes-upstream-map-2026-05-23.md` | locked by ADR-0018 |
| Lemonade adoption plan + migration | `lemonade-adoption-plan-2026-05-22.md`, `lemonade-migration-plan.md` | ADR-0006, ADR-0008; v0.2 |
| Lemonade spike findings + runbooks | `lemonade-spike-{findings,runbook,2-findings,2-runbook}-2026-05-22.md` | findings codified in ADR-0006 § "Reality vs original plan" and `hal0_lemonade_*` memories |
| Models + slots impl plan | `models-slots-impl-plan.md` | shipped across the models surface PRs |
| Session handoff 2026-05-22 | `SESSION_HANDOFF_2026-05-22.md` | Lemonade ADR-0006 reset session |
| Phase 8 pending tasks | `phase-8-pending/` | all v0.3 Hermes integration |
| Lemonade research workstream | `lemonade-research-2026-05-22/` | Lemonade adoption ADRs |

## What does NOT live here

- **ADRs** (`docs/internal/adr/`) — those are decision records, immutable, primary source of truth for architecture choices.
- **Current planning docs** (e.g. `v0.3-state.md`, `release-manifest.md`, `api-errors.md`) — live, kept up to date by recent PRs.
- **Still-useful references** — `lemonade-repo-deep-dive-2026-05-22.md` (used by the lemonade-source-spelunker subagent), `metrics-prototype.md` (referenced by `hal0_metrics_prototype_tui` memory), `primary-model-eval-2026-05-22.md` (eval results), `vue-dashboard-archive.md` (already an archive), `migration.md` (config migration docs).

## When to archive vs delete

Archive when the doc records *why* a choice was made and might be re-read
by someone diagnosing the resulting code. Delete only if the content is
both completely superseded **and** has no decision context worth preserving
(e.g. a stale TODO list with no rationale).
